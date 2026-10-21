from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import netCDF4
import numpy as np
import pytest

from wind3d_ninja.observations.models import Observation
from wind3d_ninja.pipeline import native_assimilation as native


def station_csv(tmp_path, extra="k_m2s2,epsilon_m2s3", values="1,0.1"):
    path = tmp_path / "stations.csv"
    path.write_text(
        "station_id,time_utc,latitude,longitude,height_m,height_reference,u_ms,v_ms," + extra +
        "\ns1,2025-01-01T00:00:00Z,30,103,50,agl,1,2," + values + "\n", encoding="utf-8")
    return path


def field(path, values):
    vector = np.asarray(values).ndim == 2
    body = "\n".join("("+" ".join(map(str, row))+")" for row in values) if vector else "\n".join(map(str, values))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("FoamFile { format ascii; }\ninternalField nonuniform List<"+
                    ("vector" if vector else "scalar")+f"> {len(values)}\n(\n{body}\n);\n")


def checkpoint(case, time):
    for name in ("p", "k", "epsilon", "nut", "phi"):
        field(case / str(time) / name, [0.09, 0.09] if name == "nut" else [1, 1])
    field(case / str(time) / "U", [[1, 2, 0], [1, 2, 0]])


def test_native_csv_computes_k_and_epsilon_from_explicit_statistics(tmp_path):
    path = station_csv(tmp_path, "sigma_u_ms,sigma_v_ms,sigma_w_ms,mixing_length_m", "1,2,3,2")
    record, epsilon, w, measured, epsilon_source = native._load_native_records(path)[0]
    assert record.k_m2s2 == 7
    assert epsilon == pytest.approx(0.09**0.75 * 7**1.5/2)
    assert w == 0
    assert measured is False


@pytest.mark.parametrize("columns,values", [
    ("k_m2s2", "1"), ("epsilon_m2s3", "0.1"),
    ("k_m2s2,epsilon_m2s3", "1,-1"), ("k_m2s2,mixing_length_m", "1,0"),
])
def test_native_csv_rejects_missing_or_invalid_coupled_statistics(tmp_path, columns, values):
    with pytest.raises(ValueError):
        native._load_native_records(station_csv(tmp_path, columns, values))


def test_native_preflight_rejects_missing_statistics_before_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(native, "NativeRuntime", lambda root: pytest.fail("runtime must not start"))
    with pytest.raises(ValueError, match="epsilon"):
        native.NativeCoupledBatchRunner(None).run(tmp_path, tmp_path/"out", [50], [200], 10, None, False,
                                                  station_observations=station_csv(tmp_path, "k_m2s2", "1"))


def test_native_source_uses_msl_dem_plus_physical_agl(tmp_path, monkeypatch):
    records = native._load_native_records(station_csv(tmp_path))
    monkeypatch.setattr(native, "_terrain", lambda *args: np.asarray(1800.0))
    centres = np.array([[103, 30, 1850], [103, 30, 50], [103, 30, 1860]])
    settings = {"horizontal_radius_m": 1, "vertical_radius_m": 5,
                "velocity_relaxation_seconds": 10, "tke_relaxation_seconds": 20,
                "epsilon_relaxation_seconds": 30}
    constraints, report = native._constraints(records, datetime(2025, 1, 1, tzinfo=timezone.utc), 120,
                                              native.CRS.from_epsg(4326), tmp_path/"dem", 5, centres, settings)
    assert constraints[0].cell_ids == (0,)
    assert report[0]["z_msl_m"] == 1850
    assert report[0]["physical_agl_m"] == 50
    assert report[0]["w_measured"] is False


def test_native_station_vectors_rotate_to_projected_mesh(tmp_path, monkeypatch):
    records = native._load_native_records(station_csv(tmp_path))
    monkeypatch.setattr(native, "_terrain", lambda *args: np.asarray(1800.0))
    crs = native.CRS.from_epsg(32648)
    transform = native.Transformer.from_crs(4326, crs, always_xy=True)
    x, y = transform.transform(103, 30)
    nx, ny = transform.transform(103, 30.000001)
    grid_north = np.array([nx - x, ny - y])
    grid_north /= np.linalg.norm(grid_north)
    expected = np.array([grid_north[1] + 2 * grid_north[0],
                         -grid_north[0] + 2 * grid_north[1]])
    settings = {"horizontal_radius_m": 1, "vertical_radius_m": 5,
                "velocity_relaxation_seconds": 10, "tke_relaxation_seconds": 20,
                "epsilon_relaxation_seconds": 30}
    sources, report = native._constraints(records, datetime(2025, 1, 1, tzinfo=timezone.utc), 120,
                                          crs, tmp_path / "dem", 0, np.array([[x, y, 1850]]), settings)
    np.testing.assert_allclose([sources[0].u, sources[0].v], expected, atol=1e-7)
    assert report[0]["station_true_u_ms"] == 1
    assert report[0]["station_true_v_ms"] == 2
    assert abs(report[0]["grid_north_bearing_deg"]) > 0.9


def test_native_validation_rotates_baseline_and_analysis_to_true_north(tmp_path, monkeypatch):
    target = datetime(2025, 1, 1, tzinfo=timezone.utc)
    transform = native.Transformer.from_crs(4326, 32648, always_xy=True)
    x, y = transform.transform(103, 30)
    nx, ny = transform.transform(103, 30.000001)
    north = np.array([nx - x, ny - y])
    north *= -5 / np.linalg.norm(north)
    observation = Observation("uav", "north", target, 30, 103, 50, 5, 0, moving=True)
    monkeypatch.setattr(native, "_terrain", lambda *args: np.asarray(1800.0))
    monkeypatch.setattr(native, "_sample_native", lambda *args, **kwargs:
                        np.array([[*north, 0.0, 1.0, 0.1, 0.9]]))
    rows, _ = native._validate_native(None, tmp_path, [observation], target, 50, 200, 60, 1,
                                      native.CRS.from_epsg(32648), tmp_path / "dem", 0, None, baseline_time=169)
    assert rows[0]["direction_error_deg"] < 1e-5
    assert rows[0]["native_baseline_direction_error_deg"] < 1e-5
    assert rows[0]["model_v_ms"] == pytest.approx(-5)


def test_native_validation_keeps_observed_time_provenance_for_mismatch(tmp_path):
    target = datetime(2025, 1, 1, tzinfo=timezone.utc)
    observation = Observation("uav", "flight", target.replace(hour=1), 30, 103, 50, 3, 180, moving=True,
                              metadata={"time_raw": "2025-01-01 09:00:00", "time_source_field": "pc_collection_time",
                                        "time_source_timezone_offset_hours": 8.0})
    rows, _ = native._validate_native(None, None, [observation], target, 50, 200, 60, 1,
                                      "EPSG:4326", tmp_path / "unused.tif", 0, None)
    assert rows[0]["observed_time_utc"] == observation.time_utc.isoformat()
    assert rows[0]["observed_time_raw"] == "2025-01-01 09:00:00"
    assert rows[0]["observed_time_source_field"] == "pc_collection_time"
    assert rows[0]["observed_time_source_timezone_offset_hours"] == 8.0


def test_native_solver_requires_advanced_checkpoint_and_all_converged_equations(tmp_path):
    checkpoint(tmp_path, 10)
    log = tmp_path/"log.simpleFoam"
    log.write_text("SIMPLE solution converged\n")
    with pytest.raises(RuntimeError, match="equations"):
        native._solver_state(tmp_path, log, 0, 1e-5)
    log.write_text("\n".join("Solving for "+name+", Initial residual = 1e-6" for name in ("p","Ux","Uy","Uz","k","epsilon"))+
                   "\ntime step continuity errors : sum local = 1e-7, global = 1e-9, cumulative = 1e-8\nSIMPLE solution converged\n")
    result = native._solver_state(tmp_path, log, 0, 1e-5)
    assert result["time"] == 10
    with pytest.raises(RuntimeError, match="advance"):
        native._solver_state(tmp_path, log, 10, 1e-5)


def test_native_sets_reads_actual_fields_and_omits_unreported_locations(tmp_path):
    checkpoint(tmp_path, 10)
    (tmp_path/"system").mkdir()
    points = np.array([[1, 2, 1850], [3, 4, 1850]])
    class Runtime:
        library_dirs = [tmp_path/"中文运行目录/lib"]
        def run(self, name, args, case, log, progress):
            assert args[args.index("-fields")+1] == "(U k epsilon nut)"
            dictionary = (case / "system/sampleNative").read_text()
            assert 'libs ("libsampling.dll");' in dictionary
            assert str(self.library_dirs[0]) not in dictionary
            folder = case/"postProcessing/sampleNative/10"
            folder.mkdir(parents=True)
            (folder/"nativeGrid_U.xy").write_text("1 2 1850 4 5 6\n")
            (folder/"nativeGrid_epsilon_k_nut.xy").write_text("1 2 1850 0.1 2 3.6\n")
    sampled = native._sample_native(Runtime(), tmp_path, points, None, "analysis")
    np.testing.assert_allclose(sampled[0], [4, 5, 6, 2, 0.1, 3.6])
    assert np.isnan(sampled[1]).all()


def test_native_netcdf_labels_only_actual_coupled_fields(tmp_path):
    prior, output = tmp_path/"background.nc", tmp_path/"native.nc"
    with netCDF4.Dataset(prior, "w") as ds:
        for name, size in (("height",1),("lat",1),("lon",2)):
            ds.createDimension(name,size)
        for name in ("U_wind","V_wind"):
            ds.createVariable(name,"f4",("height","lat","lon"))[:] = 99
    baseline = np.array([[1,2,3,4,5,0.288],[1,2,3,4,5,0.288]])
    analysis = baseline+1
    native._write_native_nc(prior,output,baseline,analysis,np.array([[1850,1850]]),{}, {})
    with netCDF4.Dataset(output) as ds:
        assert ds.assimilation_coupled_to_rans == "true"
        np.testing.assert_allclose(ds["U_wind"][0],[[2,2]])
        np.testing.assert_allclose(ds["TKE"][0],[[5,5]])
        np.testing.assert_allclose(ds["epsilon"][0],[[6,6]])
        assert "TKE_analysis" not in ds.variables


def test_native_batch_runs_zero_source_then_coupled_continuation(tmp_path, monkeypatch):
    import json
    import re
    captured = []
    owned = tmp_path/"out/terrain/native_domain.tif"
    dem = tmp_path/"dem.tif"
    dem.write_bytes(b"owned DEM")
    crs = native.CRS.from_epsg(32648)
    px, py = native.Transformer.from_crs(4326, crs, always_xy=True).transform(103,30)
    monkeypatch.setattr(native, "load_instrument_catalog", lambda *args, **kwargs: SimpleNamespace(records=[]))
    monkeypatch.setattr(native.DemManager, "get_dem", lambda *args, **kwargs: dem)
    monkeypatch.setattr(native, "_terrain", lambda dem,x,y,crs: np.full(np.asarray(x).shape,1800.0))
    class Runtime:
        root = tmp_path/"runtime"
        boundary_library = tmp_path/"boundary.dll"
        def __init__(self, root):
            pass
        def run(self, name, args, case, log, progress):
            control = (case/"system/controlDict").read_text()
            start = float(re.search(r"startTime\s+([^;]+);",control)[1])
            warmup = log.endswith(".warmup")
            final = float(re.search(r"endTime\s+([^;]+);",control)[1]) if warmup else start+10
            solution = (case/"system/fvSolution").read_text()
            assert ("residualControl" not in solution) if warmup else ("residualControl" in solution)
            checkpoint(case,final)
            options = (case/"constant/fvOptions").read_text()
            coupled = "station_" in options
            captured.append((coupled,start))
            result = case/log
            result.write_text("\n".join("Solving for "+field+", Initial residual = 1e-6" for field in ("p","Ux","Uy","Uz","k","epsilon"))+
                              "\ntime step continuity errors : sum local = 1e-7, global = 1e-9, cumulative = 1e-8\nSIMPLE solution converged\nstationNudging\n"+
                              "\n".join(re.findall(r"\b(station_[0-9a-f]+)\s*\{",options)))
            return result
    monkeypatch.setattr(native, "NativeRuntime", Runtime)
    def prepare(source,case,library):
        (case/"system").mkdir(parents=True)
        (case/"constant/polyMesh").mkdir(parents=True)
        (case/"system/controlDict").write_text("startTime 0;\nendTime 1000;")
        checkpoint(case,0)
        return {"checkpoint_time":"309"}
    monkeypatch.setattr(native, "prepare_native_case", prepare)
    monkeypatch.setattr(native, "_cell_geometry", lambda *args: (np.array([[px,py,1850],[px,py,1852]]),np.array([1,1])))
    monkeypatch.setattr(native, "_sample_native", lambda runtime,case,points,progress,label:
                        np.tile([1,2,0,1,0.1,0.9] if label=="baseline" else [2,3,0,2,0.2,1.8],(len(points),1)))
    class Background:
        def __init__(self,*args):
            pass
        def run(self,*args,**kwargs):
            staged_owned = args[1].parent/"terrain/native_domain.tif"
            assert kwargs["dem_override"] == staged_owned
            assert staged_owned.read_bytes() == dem.read_bytes()
            assert args[6] is False
            folder = staged_owned.parent/"NINJAFOAM_native_1"
            folder.mkdir()
            checkpoint(folder,309)
            nc = tmp_path/"background.nc"
            with netCDF4.Dataset(nc,"w") as ds:
                ds.target_time_utc = "2025-01-01T00:00:00Z"
                ds.output_height_m = 50
                ds.mesh_resolution_m = 200
                for name,size in (("height",1),("lat",1),("lon",1)):
                    ds.createDimension(name,size)
                ds.createVariable("x","f8",("lon",))[:] = px
                ds.createVariable("y","f8",("lat",))[:] = py
                ds.createVariable("crs","i4").crs_wkt = crs.to_wkt()
                for name in ("U_wind","V_wind"):
                    ds.createVariable(name,"f4",("height","lat","lon"))[:] = 10
            report = tmp_path/"report.json"
            report.write_text(json.dumps({"instrument_policy":{"canopy_height_m":0},
                                         "validation_window_seconds":120,
                                         "validation_height_tolerance_m":10,"profile_roughness_m":0.1}))
            return SimpleNamespace(netcdf_files=(nc,),report_json=report,validation_observations=())
    monkeypatch.setattr(native,"TurbulenceBatchRunner",Background)
    result = native.NativeCoupledBatchRunner(SimpleNamespace(dem=None)).run(
        tmp_path,tmp_path/"out",[50],[200],10,None,False,station_observations=station_csv(tmp_path))
    assert captured == [(False,0),(False,50),(True,60),(True,110)]
    assert result.completed == 1
    report = json.loads(result.report_json.read_text())
    assert report["cases"][0]["analysis"]["active_source_names"]
    with netCDF4.Dataset(result.netcdf_files[0]) as ds:
        assert float(ds["TKE"][0,0,0]) == 2
        assert float(ds["TKE_native_baseline"][0,0,0]) == 1


def test_native_csv_accepts_meanwind_stations_alongside_real_tke_station(tmp_path):
    path = station_csv(tmp_path)
    with path.open("a",encoding="utf-8") as handle:
        handle.write("wind_only,2025-01-01T00:00:00Z,30,104,50,agl,2,3,,\n")
    records = native._load_native_records(path)
    assert len(records) == 2
    assert records[1][0].k_m2s2 is None
    assert records[1][1] is None


def test_native_solver_rejects_nonpositive_turbulence_or_inconsistent_nut(tmp_path):
    checkpoint(tmp_path, 10)
    log = tmp_path/"log.simpleFoam"
    log.write_text("\n".join("Solving for "+name+", Initial residual = 1e-6" for name in ("p","Ux","Uy","Uz","k","epsilon"))+
                   "\ntime step continuity errors : sum local = 1e-7, global = 1e-9, cumulative = 1e-8\nSIMPLE solution converged\n")
    field(tmp_path/"10/nut",[1,1])
    with pytest.raises(ValueError,match="nut is inconsistent"):
        native._solver_state(tmp_path,log,0,1e-5)
    field(tmp_path/"10/k",[-1,1])
    with pytest.raises(ValueError,match="positive"):
        native._solver_state(tmp_path,log,0,1e-5)


def test_native_solver_requires_finite_mass_projection_below_tolerance(tmp_path):
    checkpoint(tmp_path,10)
    log = tmp_path/"log.simpleFoam"
    residuals = "\n".join("Solving for "+name+", Initial residual = 1e-6" for name in ("p","Ux","Uy","Uz","k","epsilon"))
    log.write_text(residuals+"\nSIMPLE solution converged\n")
    with pytest.raises(RuntimeError,match="continuity projection"):
        native._solver_state(tmp_path,log,0,1e-5)
    log.write_text(residuals+"\ntime step continuity errors : sum local = 0.02, global = 0.001, cumulative = 0.001\nSIMPLE solution converged\n")
    with pytest.raises(RuntimeError,match="mass continuity"):
        native._solver_state(tmp_path,log,0,1e-5)


def test_native_sources_select_nearest_station_time_and_preserve_height_gates(tmp_path, monkeypatch):
    path = station_csv(tmp_path)
    path.write_text(
        "station_id,time_utc,latitude,longitude,height_m,height_reference,u_ms,v_ms,k_m2s2,epsilon_m2s3\n"
        "s1,2025-01-01T00:00:00Z,30,103,50,agl,1,2,1,0.1\n"
        "s1,2025-01-01T00:02:00Z,30,103,50,agl,9,8,2,0.2\n"
        "s1,2025-01-01T00:01:20Z,30,103,60,agl,3,4,3,0.3\n", encoding="utf-8")
    monkeypatch.setattr(native, "_terrain", lambda *args: np.asarray(1800.0))
    settings = {"horizontal_radius_m":1,"vertical_radius_m":2,"velocity_relaxation_seconds":10,
                "tke_relaxation_seconds":10,"epsilon_relaxation_seconds":10}
    centres = np.array([[103,30,1850],[103,30,1860]])
    target = datetime(2025,1,1,0,1,tzinfo=timezone.utc)
    sources, report = native._constraints(
        native._load_native_records(path),target,120,native.CRS.from_epsg(4326),
        tmp_path/"dem",0,centres,settings)
    assert len(sources) == 2
    assert sources[0].cell_ids == (0,)
    assert sources[0].u == 1  # Equal-distance observations select the earlier time.
    assert sources[1].cell_ids == (1,)
    assert sources[1].u == 3
    assert report[0]["window_candidate_count"] == 2
    assert report[0]["time_offset_seconds"] == -60
    assert report[0]["selected_time_utc"] == "2025-01-01T00:00:00+00:00"
    assert report[1]["window_candidate_count"] == 1
    target = datetime(2025,1,1,0,1,50,tzinfo=timezone.utc)
    sources, report = native._constraints(
        native._load_native_records(path),target,120,native.CRS.from_epsg(4326),
        tmp_path/"dem",0,centres,settings)
    assert sources[0].u == 9
    assert report[0]["time_offset_seconds"] == 10


@pytest.mark.parametrize("failure", ["partial", "exception"])
def test_native_warmup_failure_restores_residual_controls_and_stops(tmp_path, failure):
    (tmp_path/"system").mkdir()
    original = "SIMPLE { residualControl { p 1e-5; U 1e-5; k 1e-5; epsilon 1e-5; } }\n"
    (tmp_path/"system/fvSolution").write_text(original)
    (tmp_path/"system/controlDict").write_text("startTime 0;\nendTime 1000;")
    checkpoint(tmp_path,0)
    calls = []
    class Runtime:
        def run(self,name,args,case,log,progress):
            calls.append(log)
            assert "residualControl" not in (case/"system/fvSolution").read_text()
            if failure == "exception":
                raise RuntimeError("warmup process failed")
            checkpoint(case,49)
    with pytest.raises(RuntimeError,match="warmup"):
        native._solve_native(Runtime(),tmp_path,0,1000,1e-5,None,"analysis")
    assert (tmp_path/"system/fvSolution").read_text() == original
    assert calls == ["log.analysis.warmup"]


def test_native_warmup_budget_minimum_and_one_remaining_convergence_step(tmp_path, monkeypatch):
    class NoRuntime:
        def run(self,*args):
            pytest.fail("budget below 51 must not run")
    with pytest.raises(ValueError,match="51"):
        native._solve_native(NoRuntime(),tmp_path,0,50,1e-5,None,"analysis")
    (tmp_path/"system").mkdir()
    original = "SIMPLE { residualControl { p 1e-5; U 1e-5; k 1e-5; epsilon 1e-5; } }\n"
    (tmp_path/"system/fvSolution").write_text(original)
    (tmp_path/"system/controlDict").write_text("startTime 0;\nendTime 1000;")
    checkpoint(tmp_path,0)
    boundaries = []
    class Runtime:
        def run(self,name,args,case,log,progress):
            import re
            text = (case/"system/controlDict").read_text()
            start = float(re.search(r"startTime\s+([^;]+);",text)[1])
            end = float(re.search(r"endTime\s+([^;]+);",text)[1])
            boundaries.append((start,end))
            checkpoint(case,end)
            result = case/log
            result.write_text("mock solver output")
            return result
    monkeypatch.setattr(native,"_solver_state",lambda case,log,start,tol: {"time":51,"converged":True})
    _, state = native._solve_native(Runtime(),tmp_path,0,51,1e-5,None,"analysis")
    assert boundaries == [(0,50),(50,51)]
    assert state["warmup_iterations"] == 50
    assert state["total_iterations"] == 51
    assert (tmp_path/"system/fvSolution").read_text() == original


def test_native_cli_rejects_budget_below_coupled_warmup(tmp_path):
    from click.testing import CliRunner
    from wind3d_ninja.cli.main import cli
    result = CliRunner().invoke(cli,["native-assimilation",str(tmp_path),"--height","50",
                                    "--station-observations",str(station_csv(tmp_path)),
                                    "--max-iterations","50"])
    assert result.exit_code == 2
    assert "--max-iterations" in result.output
    assert "51" in result.output


def test_native_heldout_validation_samples_actual_xyz_and_deduplicates_time_rows(tmp_path, monkeypatch):
    from datetime import timedelta
    from wind3d_ninja.observations.models import Observation
    target = datetime(2025,1,1,tzinfo=timezone.utc)
    def observation(name, time, av, metadata):
        return Observation("uav",name,time,30,103,av,2,180,moving=True,metadata=metadata)
    observations = [
        observation("first",target,48,{"instrument_agl_m":53,"height_method":"relative_to_launch"}),
        observation("repeat",target+timedelta(seconds=10),48,{"instrument_agl_m":53}),
        observation("second",target,52,{}),
        observation("late",target+timedelta(seconds=121),48,{"instrument_agl_m":53}),
        observation("high",target,61,{"instrument_agl_m":66}),
    ]
    monkeypatch.setattr(native,"_terrain",lambda *args: np.asarray(1800.0))
    captured = {}
    def sample(runtime,case,points,progress,label,**kwargs):
        captured.update(points=points,label=label,kwargs=kwargs)
        # First unique position is inside native CFD; second is genuinely absent.
        return np.array([[1,2,0,1,0.1,0.9],[np.nan]*6])
    monkeypatch.setattr(native,"_sample_native",sample)
    rows, summary = native._validate_native(None,tmp_path,observations,target,50,200,120,10,
                                            native.CRS.from_epsg(4326),tmp_path/"dem",5,None)
    np.testing.assert_allclose(captured["points"],[[103,30,1853],[103,30,1857]])
    assert captured["kwargs"] == {"sample_name":"sampleNativeValidation","set_name":"nativeValidation","allow_empty":True}
    assert [row["status"] for row in rows] == ["ok","ok","outside_or_invalid_native_mesh","time_mismatch","height_mismatch"]
    assert rows[0]["sample_z_msl_m"] == 1853  # Actual 53 m physical AGL, not target 50 AV.
    assert rows[2]["observed_physical_agl_m"] == 57  # Missing metadata uses observed AV + canopy.
    assert summary["eligible_count"] == 3
    assert summary["unique_point_count"] == 2
    assert summary["sampled_count"] == 2


def test_native_heldout_validation_does_not_sample_mismatched_points(tmp_path, monkeypatch):
    from wind3d_ninja.observations.models import Observation
    target = datetime(2025,1,1,tzinfo=timezone.utc)
    observations = [Observation("uav","high",target,30,103,70,2,180,moving=True)]
    monkeypatch.setattr(native,"_sample_native",lambda *args,**kwargs: pytest.fail("no eligible native points"))
    rows, summary = native._validate_native(None,tmp_path,observations,target,50,200,120,10,
                                            native.CRS.from_epsg(4326),tmp_path/"dem",5,None)
    assert rows[0]["status"] == "height_mismatch"
    assert summary["eligible_count"] == 0


def test_native_heldout_baseline_and_analysis_use_identical_actual_xyz(tmp_path, monkeypatch):
    from wind3d_ninja.observations.models import Observation
    target = datetime(2025,1,1,tzinfo=timezone.utc)
    observations = [Observation("uav","check",target,30,103,48,2,180,moving=True,
                                metadata={"instrument_agl_m":53})]
    monkeypatch.setattr(native,"_terrain",lambda *args: np.asarray(1800.0))
    calls = []
    def sample(runtime,case,points,progress,label,**kwargs):
        calls.append((points.copy(),label,kwargs.get("sample_time")))
        return np.array([[0,3,0,1,0.1,0.9]]) if kwargs.get("sample_time")==169 else np.array([[0,4,0,1,0.1,0.9]])
    monkeypatch.setattr(native,"_sample_native",sample)
    rows, summary = native._validate_native(None,tmp_path,observations,target,50,200,120,10,
                                            native.CRS.from_epsg(4326),tmp_path/"dem",5,None,baseline_time=169)
    np.testing.assert_array_equal(calls[0][0],calls[1][0])
    np.testing.assert_allclose(calls[0][0],[[103,30,1853]])
    assert calls[1][2] == 169
    assert rows[0]["native_baseline_speed_ms"] == 3
    assert rows[0]["model_speed_ms"] == 4
    assert rows[0]["native_baseline_speed_error_ms"] == 1
    assert rows[0]["speed_error_ms"] == 2
    assert summary["native_baseline_sampled_count"] == 1
