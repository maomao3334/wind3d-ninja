from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import shutil

import numpy as np


def _text(path: Path) -> str:
    text = Path(path).read_text(encoding="utf-8")
    # Preserve quoted strings, and replace comments with whitespace (token separator).
    text = re.sub(r'"(?:\\.|[^"\\])*"|/\*.*?\*/|//[^\n]*',
                  lambda m: m[0] if m[0].startswith('"') else " ", text, flags=re.S)
    if re.search(r"\bformat\s+binary\s*;", text):
        raise ValueError(f"Binary OpenFOAM input is unsupported: {path}")
    return text


def _values(body: str, vector: bool) -> np.ndarray:
    tokens = re.findall(r"\(|\)|[^\s()]+", body)
    try:
        if vector:
            rows = []
            for offset in range(0, len(tokens), 5):
                if len(tokens) - offset < 5 or tokens[offset] != "(" or tokens[offset + 4] != ")":
                    raise ValueError("Malformed vector list")
                rows.append([float(x) for x in tokens[offset + 1:offset + 4]])
            values = np.asarray(rows, dtype=float).reshape(-1, 3)
        else:
            values = np.asarray([float(x) for x in tokens], dtype=float)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Malformed OpenFOAM numeric values") from exc
    if not np.isfinite(values).all():
        raise ValueError("OpenFOAM values must be finite")
    return values


def read_foam_field(path: Path, count: int | None = None) -> np.ndarray:
    """Read an ASCII scalar/vector internalField, validating its declared size."""
    if count is not None and (not isinstance(count, int) or count < 0):
        raise ValueError("count must be a nonnegative integer")
    text = _text(path)
    uniform = re.search(r"\binternalField\s+uniform\s+([^;]+);", text)
    if uniform:
        body = uniform[1].strip()
        values = _values(body, body.startswith("("))
        if len(values) != 1:
            raise ValueError("Uniform field must contain one value")
        return np.repeat(values, count if count is not None else 1, axis=0)
    match = re.search(r"\binternalField\s+nonuniform\s+List<(scalar|vector)>\s+(\d+)\s*\((.*?)\)\s*;", text, re.S)
    if not match:
        raise ValueError(f"Missing or unsupported internalField: {path}")
    values = _values(match[3], match[1] == "vector")
    if len(values) != int(match[2]) or (count is not None and len(values) != count):
        raise ValueError("OpenFOAM field count mismatch")
    return values


def read_foam_points(path: Path) -> np.ndarray:
    text = _text(path)
    match = re.search(r"(?:^|[}\s])([0-9]+)\s*\((.*)\)\s*;?\s*$", text, re.S)
    if not match:
        raise ValueError(f"Missing counted points list: {path}")
    values = _values(match[2], True)
    if len(values) != int(match[1]):
        raise ValueError("OpenFOAM points count mismatch")
    return values


def _header(name: str, cls: str = "dictionary", location: str | None = None) -> str:
    loc = f'    location "{location}";\n' if location else ""
    return f"FoamFile\n{{\n    version 2.0;\n    format ascii;\n    class {cls};\n{loc}    object {name};\n}}\n"


def _patch_blocks(text: str) -> dict[str, tuple[int, int]]:
    boundary = re.search(r"\bboundaryField\s*\{", text)
    if not boundary:
        return {}
    tokens = list(re.finditer(r'"(?:\\.|[^"\\])*"|[{}]|[^\s{}]+', text[boundary.end():]))
    patches = {}
    depth = 0
    name = None
    start = 0
    for token in tokens:
        value = token[0]
        if value == "{":
            if depth == 0:
                start = boundary.end() + token.end()
            depth += 1
        elif value == "}":
            if depth == 0:
                break
            depth -= 1
            if depth == 0 and name is not None:
                patches[name] = (start, boundary.end() + token.start())
        elif depth == 0:
            name = value.strip('"')
    return patches


def _migrate_profile_coefficients(case: Path) -> list[dict]:
    required = ("UfreeStream", "uDirection", "inputWindHeight_Veg", "z0", "Rd", "firstCellHeight")
    velocity = _text(Path(case) / "0" / "U")
    velocity_patches = _patch_blocks(velocity)
    adaptations = []
    for field, profile in (("k", "logProfileTurbulentKineticEnergyInlet"),
                           ("epsilon", "logProfileDissipationRateInlet")):
        path = Path(case) / "0" / field
        text = _text(path)
        insertions = []
        for patch, (start, end) in _patch_blocks(text).items():
            body = text[start:end]
            if not re.search(rf"\btype\s+{profile}\s*;", body):
                continue
            missing = [key for key in required if not re.search(rf"\b{key}\s+[^;]+;", body)]
            if not missing:
                continue
            if patch not in velocity_patches:
                raise ValueError(f"Cannot migrate {field}/{patch}: no matching U patch")
            ustart, uend = velocity_patches[patch]
            ubody = velocity[ustart:uend]
            if not re.search(r"\btype\s+logProfileVelocityInlet\s*;", ubody):
                raise ValueError(f"Cannot migrate {field}/{patch}: U patch is not a log profile")
            added = []
            for key in missing:
                entry = re.search(rf"\b{key}\s+([^;]+);", ubody)
                if not entry:
                    raise ValueError(f"Cannot migrate {field}/{patch}: U profile missing {key}")
                value = entry[1].strip()
                added.append(f"        {key} {value};\n")
                adaptations.append({"field": field, "patch": patch, "coefficient": key,
                                    "value": value, "source": f"0/U/boundaryField/{patch}"})
            insertions.append((end, "".join(added)))
        if insertions:
            for offset, addition in reversed(insertions):
                text = text[:offset] + addition + text[offset:]
            path.write_text(text, encoding="utf-8")
    return adaptations


def prepare_native_case(source: Path, dest: Path, boundary_library: Path) -> dict:
    """Reconstruct the latest complete serial checkpoint with cumulative mesh changes."""
    boundary_library = Path(boundary_library).resolve()
    if not boundary_library.is_file():
        raise FileNotFoundError(f"Native boundary library missing: {boundary_library}")
    library = boundary_library.name
    if not library.isascii() or '"' in library:
        raise ValueError("Native boundary DLL filename must be ASCII without quotes")
    source, dest = Path(source).resolve(), Path(dest).resolve()
    if source == dest or source in dest.parents or dest in source.parents:
        raise ValueError("Native case must be separate from the source case")
    if dest.exists() and any(dest.iterdir()):
        raise ValueError("Native case destination must be empty")
    fields = ("U", "p", "k", "epsilon", "nut", "phi")
    times = []
    for path in source.iterdir():
        if path.is_dir():
            try:
                value = float(path.name)
            except ValueError:
                continue
            if np.isfinite(value):
                times.append((value, path))
    times.sort(key=lambda item: (item[0], item[1].name))
    checkpoints = [(time, path) for time, path in times if all((path / f).is_file() for f in fields)]
    if not checkpoints:
        raise ValueError("No complete serial U/p/k/epsilon/nut/phi checkpoint")
    time, checkpoint = checkpoints[-1]
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("constant", "system"):
        shutil.copytree(source / name, dest / name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("processor*"))
    (dest / "0").mkdir()
    for field in fields:
        shutil.copy2(checkpoint / field, dest / "0" / field)
    boundary_adaptations = _migrate_profile_coefficients(dest)
    scheme_adaptations = []
    schemes_path = dest / "system" / "fvSchemes"
    if schemes_path.is_file():
        schemes = _text(schemes_path)
        old_key = "div((nuEff*dev(T(grad(U)))))"
        new_key = "div((nuEff*dev2(T(grad(U)))))"
        if new_key not in schemes and old_key in schemes:
            schemes = schemes.replace(old_key, new_key)
            schemes_path.write_text(schemes, encoding="utf-8")
            scheme_adaptations.append({"from": old_key, "to": new_key,
                                       "reason": "OpenFOAM 8 deviatoric stress lookup; discretisation retained"})
    mesh_times = ["constant"]
    for value, path in times:
        if value <= time and (path / "polyMesh").is_dir():
            shutil.copytree(path / "polyMesh", dest / "constant" / "polyMesh", dirs_exist_ok=True)
            mesh_times.append(path.name)
    (dest / "constant" / "momentumTransport").write_text(
        _header("momentumTransport") + "simulationType RAS;\nRAS\n{\n    model stationKEpsilon;\n    turbulence on;\n    printCoeffs on;\n}\n", encoding="utf-8")
    control_path = dest / "system" / "controlDict"
    control = _text(control_path)
    replacements = {"application": "simpleFoam", "startFrom": "startTime", "startTime": "0",
                    "writeFormat": "ascii", "writeCompression": "off"}
    for key, value in replacements.items():
        pattern = rf"\b{key}\s+[^;]+;"
        control = re.sub(pattern, f"{key} {value};", control) if re.search(pattern, control) else control + f"\n{key} {value};\n"
    control = re.sub(r"\blibs\s*\([^)]*\)\s*;?", "", control, flags=re.S)
    control_path.write_text(control + f'\nlibs ("{library}");\n', encoding="utf-8")
    warnings = []
    if "nutkNonEqWallFunction" in _text(dest / "0" / "nut"):
        warnings.append("nutkNonEqWallFunction requires compatibility verification with the native boundary library")
    return {"checkpoint_time": checkpoint.name, "mesh_times": mesh_times,
            "source_fields": list(fields), "warnings": warnings,
            "boundary_adaptations": boundary_adaptations,
            "scheme_adaptations": scheme_adaptations}


@dataclass(frozen=True)
class NativeCellConstraint:
    station_id: str
    cell_ids: tuple[int, ...]
    u: float
    v: float
    w: float = 0.0
    k: float | None = None
    epsilon: float | None = None
    velocity_relaxation_seconds: float = 30.0
    tke_relaxation_seconds: float = 30.0
    epsilon_relaxation_seconds: float = 30.0
    constrain_velocity: bool = True


def write_station_fv_options(case: Path, constraints: list[NativeCellConstraint]) -> Path:
    """Write independent additive sources; overlapping stations remain independent."""
    chunks = [_header("fvOptions")]
    prepared = []
    for index, constraint in enumerate(constraints):
        times = (constraint.velocity_relaxation_seconds, constraint.tke_relaxation_seconds,
                 constraint.epsilon_relaxation_seconds)
        if any(not np.isfinite(seconds) or seconds <= 0 for seconds in times):
            raise ValueError("Relaxation times must be finite and positive")
        cells = sorted(set(constraint.cell_ids))
        if not cells or any(not isinstance(cell, (int, np.integer)) or isinstance(cell, bool) or cell < 0 for cell in cells):
            raise ValueError("Station selection requires nonnegative integer cell ids")
        values = [("U", (constraint.u, constraint.v, constraint.w), constraint.velocity_relaxation_seconds)] if constraint.constrain_velocity else []
        for field, target, seconds in (("k", constraint.k, constraint.tke_relaxation_seconds),
                                       ("epsilon", constraint.epsilon, constraint.epsilon_relaxation_seconds)):
            if target is not None:
                if not np.isfinite(target) or target <= 0:
                    raise ValueError(f"{field} target must be finite and positive")
                values.append((field, target, seconds))
        if not values:
            raise ValueError("Station constraint must target at least one field")
        name = "station_" + hashlib.sha256(f"{constraint.station_id}:{index}".encode()).hexdigest()[:16]
        entries = []
        for field, target, seconds in values:
            if not np.isfinite(seconds) or seconds <= 0 or not np.isfinite(target).all():
                raise ValueError("Source targets must be finite and relaxation times positive")
            rate = 1.0 / seconds
            explicit = "(" + " ".join(f"{x * rate:.17g}" for x in target) + ")" if field == "U" else f"{target * rate:.17g}"
            entries.append(f"        {field}\n        {{\n            explicit {explicit};\n            implicit {-rate:.17g};\n        }}\n")
        chunks.append(f"\n{name}\n{{\n    type stationNudging;\n    active yes;\n    selectionMode cellSet;\n    cellSet {name};\n    volumeMode specific;\n    sources\n    {{\n" + "".join(entries) + "    }\n}\n")
        prepared.append((name, cells))
    sets = Path(case) / "constant" / "polyMesh" / "sets"
    sets.mkdir(parents=True, exist_ok=True)
    for name, cells in prepared:
        (sets / name).write_text(_header(name, "cellSet", "constant/polyMesh/sets") +
                                f"{len(cells)}\n(\n" + "\n".join(map(str, cells)) + "\n)\n", encoding="utf-8")
    path = Path(case) / "constant" / "fvOptions"
    path.write_text("".join(chunks), encoding="utf-8")
    return path


def configure_native_solver(case: Path, max_iterations: int, residual_tolerance: float = 1e-5) -> dict:
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or max_iterations < 1:
        raise ValueError("max_iterations must be a positive integer")
    if not np.isfinite(residual_tolerance) or residual_tolerance <= 0:
        raise ValueError("residual_tolerance must be finite and positive")
    control_path = Path(case) / "system" / "controlDict"
    previous_control = _text(control_path)
    libraries = re.search(r"\blibs\s*\(([^)]*)\)", previous_control, re.S)
    control = _header("controlDict")
    settings = {"application": "simpleFoam", "startFrom": "startTime", "startTime": "0",
                "stopAt": "endTime", "endTime": str(max_iterations), "deltaT": "1",
                "writeControl": "timeStep", "writeInterval": str(max_iterations),
                "purgeWrite": "0", "writeFormat": "ascii", "writePrecision": "12",
                "writeCompression": "off", "runTimeModifiable": "false"}
    for key, value in settings.items():
        control += f"{key} {value};\n"
    if libraries:
        control += f"libs ({libraries[1]});\n"
    solution_path = Path(case) / "system" / "fvSolution"
    solution = _header("fvSolution") + "solvers\n{\n    p\n    {\n        solver GAMG;\n        tolerance 1e-8;\n        relTol 0.01;\n        smoother GaussSeidel;\n    }\n"
    for field in ("U", "k", "epsilon"):
        solution += f"    {field}\n    {{\n        solver smoothSolver;\n        smoother symGaussSeidel;\n        tolerance 1e-9;\n        relTol 0;\n    }}\n"
    solution += "}\nSIMPLE\n{\n    nNonOrthogonalCorrectors 0;\n    consistent no;\n    pRefCell 0;\n    pRefValue 0;\n    residualControl\n    {\n"
    for field in ("p", "U", "k", "epsilon"):
        solution += f"        {field} {residual_tolerance:.17g};\n"
    solution += "    }\n}\nrelaxationFactors\n{\n    fields { p 0.3; }\n    equations { U 0.5; k 0.5; epsilon 0.5; }\n}\n"
    control_path.write_text(control, encoding="utf-8")
    solution_path.write_text(solution, encoding="utf-8")
    return {"max_iterations": max_iterations, "residual_tolerance": residual_tolerance}
