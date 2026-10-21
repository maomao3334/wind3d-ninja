from pathlib import Path

import numpy as np
import pytest

from wind3d_ninja.windninja.foam import (
    NativeCellConstraint, configure_native_solver, prepare_native_case, read_foam_field,
    read_foam_points, write_station_fv_options,
)


def test_ascii_fields_and_points(tmp_path):
    field = tmp_path / "U"
    field.write_text("internalField nonuniform List<vector> 2 ((1/*join*/ 2 3) (-4 5 6));")
    np.testing.assert_equal(read_foam_field(field, 2), [[1, 2, 3], [-4, 5, 6]])
    with pytest.raises(ValueError, match="count"):
        read_foam_field(field, 3)
    field.write_text("internalField uniform -2;")
    np.testing.assert_equal(read_foam_field(field, 3), [-2, -2, -2])
    field.write_text("internalField uniform (1 nan 3);")
    with pytest.raises(ValueError, match="finite"):
        read_foam_field(field)
    points = tmp_path / "points"
    points.write_text("FoamFile { format ascii; }\n2\n((100 200 300) (101 201 301))")
    np.testing.assert_equal(read_foam_points(points), [[100, 200, 300], [101, 201, 301]])


def test_checkpoint_mesh_merge(tmp_path):
    source, dest = tmp_path / "source", tmp_path / "dest"
    (source / "constant" / "polyMesh").mkdir(parents=True)
    (source / "constant" / "polyMesh" / "faces").write_text("original faces")
    (source / "constant" / "polyMesh" / "points").write_text("flat")
    (source / "system").mkdir()
    (source / "system" / "controlDict").write_text("application simpleFoam; libs (\"old.so\");")
    (source / "system" / "fvSchemes").write_text("divSchemes { div((nuEff*dev(T(grad(U))))) Gauss linear; }")
    for time, file, text in (("50", "faces", "new faces"), ("51", "owner", "new owner"),
                             ("52", "points", "terrain"), ("400", "points", "future")):
        (source / time / "polyMesh").mkdir(parents=True)
        (source / time / "polyMesh" / file).write_text(text)
    (source / "309").mkdir()
    for field in ("U", "p", "k", "epsilon", "nut", "phi"):
        (source / "309" / field).write_text("internalField uniform 1;")
    profile_values = "UfreeStream 8; uDirection (-1 0 0); inputWindHeight_Veg 50; z0 1; Rd 12; firstCellHeight 200;"
    (source / "309" / "U").write_text("internalField uniform (8 0 0); boundaryField { east { type logProfileVelocityInlet; " + profile_values + " value uniform (8 0 0); } }")
    (source / "309" / "k").write_text("internalField uniform 1; boundaryField { east { type logProfileTurbulentKineticEnergyInlet; z0 2; value uniform 1; } }")
    (source / "309" / "epsilon").write_text("internalField uniform 1; boundaryField { east { type logProfileDissipationRateInlet; " + profile_values.replace("uDirection (-1 0 0);", "") + " value uniform 1; } }")
    (source / "processor0").mkdir()
    boundary_library = tmp_path / "中文插件目录" / "boundary.dll"
    boundary_library.parent.mkdir()
    boundary_library.touch()
    result = prepare_native_case(source, dest, boundary_library)
    assert 'libs ("boundary.dll");' in (dest / "system/controlDict").read_text()
    assert str(boundary_library.parent) not in (dest / "system/controlDict").read_text()
    assert result["checkpoint_time"] == "309"
    assert result["mesh_times"] == ["constant", "50", "51", "52"]
    assert (dest / "constant" / "polyMesh" / "faces").read_text() == "new faces"
    assert (dest / "constant" / "polyMesh" / "points").read_text() == "terrain"
    assert (source / "constant" / "polyMesh" / "points").read_text() == "flat"
    assert not (dest / "processor0").exists()
    assert 'writeFormat ascii;' in (dest / "system" / "controlDict").read_text()
    assert "div((nuEff*dev2(T(grad(U))))) Gauss linear;" in (dest / "system" / "fvSchemes").read_text()
    assert len(result["scheme_adaptations"]) == 1
    assert "uDirection (-1 0 0);" in (dest / "0" / "k").read_text()
    assert "z0 2;" in (dest / "0" / "k").read_text()
    assert "uDirection (-1 0 0);" in (dest / "0" / "epsilon").read_text()
    assert "uDirection" not in (source / "309" / "epsilon").read_text()
    assert len(result["boundary_adaptations"]) == 6
    assert result["boundary_adaptations"][-1] == {
        "field": "epsilon", "patch": "east", "coefficient": "uDirection",
        "value": "(-1 0 0)", "source": "0/U/boundaryField/east"}


def test_overlapping_sources_remain_additive(tmp_path):
    constraints = [NativeCellConstraint("a/站", (1, 2), -2, 4, k=3, epsilon=0.5,
                                        velocity_relaxation_seconds=2),
                   NativeCellConstraint("b", (2, 3), 6, 8)]
    options = write_station_fv_options(tmp_path, constraints).read_text()
    assert options.count("type stationNudging;") == 2
    assert options.count("volumeMode specific;") == 2
    assert "explicit (-1 2 0);" in options
    assert "implicit -0.5;" in options
    sets = list((tmp_path / "constant" / "polyMesh" / "sets").iterdir())
    assert len(sets) == 2
    assert all("\n2\n" in path.read_text() for path in sets)
    with pytest.raises(ValueError):
        write_station_fv_options(tmp_path, [NativeCellConstraint("bad", (), 1, 2)])
    with pytest.raises(ValueError):
        write_station_fv_options(tmp_path, [NativeCellConstraint("bad", (1,), 1, 2, k=0)])


@pytest.mark.parametrize("field", ["U", "k", "epsilon"])
def test_single_equation_ablation(tmp_path, field):
    constraint = NativeCellConstraint("ablation", (0,), 1, 2,
                                      k=0.5 if field == "k" else None,
                                      epsilon=0.1 if field == "epsilon" else None,
                                      constrain_velocity=field == "U")
    options = write_station_fv_options(tmp_path, [constraint]).read_text()
    for candidate in ("U", "k", "epsilon"):
        assert (f"        {candidate}\n" in options) == (candidate == field)
    with pytest.raises(ValueError, match="at least one"):
        write_station_fv_options(tmp_path, [NativeCellConstraint("empty", (0,), 0, 0,
                                                               constrain_velocity=False)])


def test_native_solver_limits(tmp_path):
    (tmp_path / "system").mkdir()
    control = tmp_path / "system" / "controlDict"
    control.write_text('application old; endTime 309; writeFormat binary; libs ("boundary.dll");')
    result = configure_native_solver(tmp_path, 25, 0.0001)
    assert result["max_iterations"] == 25
    assert "endTime 25;" in control.read_text()
    assert "writeFormat ascii;" in control.read_text()
    assert 'libs ("boundary.dll");' in control.read_text()
    solution = (tmp_path / "system" / "fvSolution").read_text()
    assert "solver GAMG;" in solution
    assert "residualControl" in solution
    for field in ("p", "U", "k", "epsilon"):
        assert f"        {field} 0.0001;" in solution
    with pytest.raises(ValueError):
        configure_native_solver(tmp_path, 0)


def test_native_case_rejects_missing_boundary_library_before_copy(tmp_path):
    destination = tmp_path / "case"
    with pytest.raises(FileNotFoundError, match="Native boundary library missing"):
        prepare_native_case(tmp_path / "source", destination, tmp_path / "missing.dll")
    assert not destination.exists()
