from __future__ import annotations

import json
from pathlib import Path
import traceback

import numpy as np

from wind3d_ninja.pipeline.native_assimilation import _cell_geometry, _latest_fields, _sample_native, _solve_native
from wind3d_ninja.windninja.foam import configure_native_solver, prepare_native_case, read_foam_field, write_station_fv_options
from wind3d_ninja.windninja.native import NativeRuntime


def main():
    root = Path(__file__).resolve().parents[1] / "output/native_rans_20261012"
    output = root / "中文完整求解"
    output.mkdir(parents=True, exist_ok=True)
    case = output / "案例修复"
    runtime = NativeRuntime(root / "中文搬迁检查/openfoam8")
    report = {"runtime": str(runtime.root), "case": str(case), "passed": False}
    try:
        prepare_native_case(root / "equations_final/baseline", case, runtime.boundary_library)
        configure_native_solver(case, 3000, 1e-5)
        write_station_fv_options(case, [])
        log, state = _solve_native(runtime, case, 0, 3000, 1e-5, None, "unicode")
        report["solver"] = state
        report["solver_log"] = str(log)
        centres, volumes = _cell_geometry(runtime, case, None)
        _, checkpoint = _latest_fields(case)
        fields = {name: read_foam_field(checkpoint / name, len(centres)) for name in ("U", "k", "epsilon", "nut")}
        if not all(np.isfinite(values).all() for values in fields.values()):
            raise ValueError("Native final fields are not finite")
        indices = np.linspace(0, len(centres)-1, 5, dtype=int)
        points = centres[indices]
        sampled = _sample_native(runtime, case, points, None, "unicode")
        if sampled.shape != (5, 6) or not np.isfinite(sampled).all():
            raise ValueError("Native Chinese-path six-field samples are invalid")
        logs = [path for path in case.glob("log.*") if path.is_file()]
        if any("FOAM FATAL" in path.read_text(encoding="utf-8", errors="replace") for path in logs):
            raise ValueError("Native run contains FOAM FATAL")
        if not state.get("converged", False):
            raise ValueError("Native full solve did not converge")
        text = Path(log).read_text(encoding="utf-8", errors="replace")
        if "stationKEpsilon" not in text:
            raise ValueError("Custom native turbulence plugin was not selected")
        report.update({"passed": True, "plugin_selected": "stationKEpsilon", "sample_points": points.tolist(),
                       "sample_values_U_k_epsilon_nut": sampled.tolist(), "sample_shape": list(sampled.shape),
                       "finite_final_fields": True, "cell_count": len(centres), "log_files": [str(path) for path in logs]})
        np.savetxt(output / "native_samples.csv", np.column_stack((points, sampled)), delimiter=",")
    except Exception:
        report["error"] = traceback.format_exc()
        raise
    finally:
        (output / "qa_manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Chinese-path full solve, plugin loading and five-point six-field sampling PASS", flush=True)


if __name__ == "__main__":
    main()
