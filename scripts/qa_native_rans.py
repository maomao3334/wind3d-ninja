from __future__ import annotations

import argparse
import csv
from dataclasses import replace
import json
from pathlib import Path

import numpy as np
from pyproj import CRS, Transformer

from wind3d_ninja.pipeline.native_assimilation import _cell_geometry, _latest_fields, _solve_native, _terrain
from wind3d_ninja.windninja.foam import NativeCellConstraint, configure_native_solver, prepare_native_case, read_foam_field, write_station_fv_options
from wind3d_ninja.windninja.native import NativeRuntime


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-case", type=Path, required=True)
    parser.add_argument("--dem", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, default=Path("runtime/openfoam8"))
    parser.add_argument("--output", type=Path, default=Path("output/native_rans_20261012/equations"))
    parser.add_argument("--iterations", type=int, default=3000)
    parser.add_argument("--tolerance", type=float, default=1e-5)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    runtime = NativeRuntime(args.runtime)
    baseline = output / "baseline"
    prepare_native_case(args.source_case, baseline, runtime.boundary_library)
    configure_native_solver(baseline, args.iterations, args.tolerance)
    write_station_fv_options(baseline, [])
    log, state = _solve_native(runtime, baseline, 0, args.iterations, args.tolerance, None, "solve")
    centres, volumes = _cell_geometry(runtime, baseline, None)
    _, checkpoint = _latest_fields(baseline)
    before = {name: read_foam_field(checkpoint/name, len(centres)) for name in ("U", "k", "epsilon", "nut")}
    crs = CRS.from_epsg(32648)
    ground = _terrain(args.dem, centres[:,0], centres[:,1], crs)
    agl = centres[:,2] - ground
    eligible = np.flatnonzero(np.isfinite(agl) & (agl > 5) & (agl < 100))
    if len(eligible) < 2:
        raise ValueError("Native mesh has too few low-altitude station cells")
    first = int(eligible[np.argmin((centres[eligible,0]-237650)**2 + (centres[eligible,1]-2772940)**2)])
    second = int(eligible[np.argmin((centres[eligible,0]-centres[first,0]-3000)**2 + (centres[eligible,1]-centres[first,1])**2)])
    if np.linalg.norm(centres[first,:2]-centres[second,:2]) < 1000:
        raise ValueError("Synthetic stations are not spatially independent")
    constraints = []
    to_geo = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    station_rows = []
    for index, sign in ((first, 1), (second, -1)):
        p = centres[index]
        ids = tuple(map(int, np.flatnonzero((np.linalg.norm(centres[:,:2]-p[:2],axis=1) <= 350) & (abs(centres[:,2]-p[2]) <= 50))))
        u = before["U"][index].copy()
        u[0] += sign * 3
        k = float(before["k"][index] * (1.8 if sign > 0 else 0.55))
        epsilon = float(before["epsilon"][index] * (1.5 if sign > 0 else 0.6))
        item = NativeCellConstraint(f"synthetic_{sign}", ids, *map(float,u), k, epsilon, 10, 10, 10)
        constraints.append(item)
        lon, lat = to_geo.transform(p[0], p[1])
        station_rows.append(dict(station_id=item.station_id,time_utc="2024-04-03T08:06:00Z",latitude=lat,longitude=lon,
                                 height_m=float(agl[index]),height_reference="agl",u_ms=item.u,v_ms=item.v,w_ms=item.w,
                                 k_m2s2=k,epsilon_m2s3=epsilon))
    with (output.parent/"synthetic_stations.csv").open("w",newline="",encoding="utf-8") as handle:
        writer = csv.DictWriter(handle,list(station_rows[0]))
        writer.writeheader()
        writer.writerows(station_rows)
    scenarios = {
        "coupled": constraints,
        "u_only": [replace(item,k=None,epsilon=None) for item in constraints],
        "k_only": [replace(item,constrain_velocity=False,epsilon=None) for item in constraints],
        "epsilon_only": [replace(item,constrain_velocity=False,k=None) for item in constraints],
        "station_a": constraints[:1], "station_b": constraints[1:],
    }
    report = {"observations": "Synthetic mechanism verification, not measured turbulence validation",
              "baseline": state, "cell_count": len(centres), "volume_m3": float(volumes.sum()), "scenarios": {}}
    for name, sources in scenarios.items():
        case = output/name
        prepare_native_case(baseline, case, runtime.boundary_library)
        configure_native_solver(case, args.iterations, args.tolerance)
        write_station_fv_options(case, sources)
        log, solved = _solve_native(runtime, case, 0, args.iterations, args.tolerance, None, "solve")
        _, checkpoint = _latest_fields(case)
        after = {field: read_foam_field(checkpoint/field,len(centres)) for field in before}
        responses = []
        for item in constraints:
            ids = np.array(item.cell_ids)
            weights = volumes[ids]
            responses.append({"station_id":item.station_id,"cell_count":len(ids),
                "baseline_u":np.average(before["U"][ids],axis=0,weights=weights).tolist(),
                "analysis_u":np.average(after["U"][ids],axis=0,weights=weights).tolist(),
                "baseline_k":float(np.average(before["k"][ids],weights=weights)),
                "analysis_k":float(np.average(after["k"][ids],weights=weights)),
                "baseline_epsilon":float(np.average(before["epsilon"][ids],weights=weights)),
                "analysis_epsilon":float(np.average(after["epsilon"][ids],weights=weights))})
        report["scenarios"][name] = {"state":solved,"responses":responses,
            "field_l2_changes":{field:float(np.linalg.norm(after[field]-before[field])) for field in before}}
        if name == "epsilon_only":
            for item, response in zip(constraints, responses):
                target_delta = item.epsilon-response["baseline_epsilon"]
                observed_delta = response["analysis_epsilon"]-response["baseline_epsilon"]
                if observed_delta*target_delta <= 0 or abs(observed_delta) < 1e-5:
                    raise AssertionError("Independent epsilon source did not produce a meaningful response toward its target")
        (output/"equation_qa.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        print(f"Native scenario passed: {name}",flush=True)
    print(f"Native equation QA: {output}",flush=True)


if __name__ == "__main__":
    main()
