"""Unit tests for split-stage pure functions: cell IDs, NN target, folds."""

import numpy as np

from radar_postproc.split import assign_folds, assign_target_nn, cell_ids


class TestCellIds:
    def test_anchored_at_origin(self):
        # Cells are floor(x/size), so IDs do not depend on data extent.
        ids = cell_ids(np.array([0.0, 499e3, 500e3, -1.0]),
                       np.array([0.0, 0.0, 0.0, 0.0]), "antarctic", 500e3)
        assert list(ids) == ["ant:0:0", "ant:0:0", "ant:1:0", "ant:-1:0"]

    def test_stable_when_extent_shifts(self):
        x = np.array([750e3])
        y = np.array([-250e3])
        alone = cell_ids(x, y, "antarctic", 500e3)
        with_more_data = cell_ids(np.append(x, -2000e3), np.append(y, 2000e3),
                                  "antarctic", 500e3)
        assert alone[0] == with_more_data[0] == "ant:1:-1"

    def test_unique_across_sheets(self):
        a = cell_ids(np.array([0.0]), np.array([0.0]), "antarctic", 500e3)
        g = cell_ids(np.array([0.0]), np.array([0.0]), "greenland", 500e3)
        assert a[0] == "ant:0:0" and g[0] == "grl:0:0"

    def test_boundary_belongs_to_upper_cell(self):
        ids = cell_ids(np.array([500e3]), np.array([-500e3]), "greenland", 500e3)
        assert ids[0] == "grl:1:-1"


class TestAssignTargetNN:
    def test_nearest_within_cutoff(self):
        grid = np.array([[0.0, 0.0], [10.0, 0.0]])
        obs = np.array([[1.0, 0.0], [8.0, 0.0]])
        vals, dists = assign_target_nn(grid, obs, np.array([10.0, 20.0]), cutoff_m=5.0)
        assert vals[0] == 10.0 and vals[1] == 20.0
        assert np.allclose(dists, [1.0, 2.0])

    def test_nan_beyond_cutoff(self):
        grid = np.array([[0.0, 0.0]])
        obs = np.array([[100.0, 0.0]])
        vals, dists = assign_target_nn(grid, obs, np.array([1.0]), cutoff_m=5.0)
        assert np.isnan(vals[0]) and np.isnan(dists[0])

    def test_no_observations(self):
        vals, dists = assign_target_nn(np.zeros((3, 2)), np.empty((0, 2)),
                                       np.empty(0), cutoff_m=5.0)
        assert np.isnan(vals).all() and np.isnan(dists).all()


class TestAssignFolds:
    def _counts(self, n_cells=25, seed=0):
        rng = np.random.default_rng(seed)
        return {f"ant:{i}:{i}": int(rng.integers(10, 1000)) for i in range(n_cells)}

    def test_deterministic(self):
        counts = self._counts()
        assert assign_folds(counts, 5, seed=42) == assign_folds(counts, 5, seed=42)
        assert assign_folds(counts, 5, seed=42) != assign_folds(counts, 5, seed=43)

    def test_every_nonempty_cell_assigned(self):
        counts = self._counts()
        counts["ant:99:99"] = 0
        folds = assign_folds(counts, 5, seed=42)
        assert set(folds) == set(counts) - {"ant:99:99"}
        assert all(0 <= f < 5 for f in folds.values())

    def test_all_folds_used_and_roughly_balanced(self):
        counts = {f"c{i}": 100 for i in range(100)}
        folds = assign_folds(counts, 5, seed=1)
        sizes = {f: sum(counts[c] for c, ff in folds.items() if ff == f) for f in range(5)}
        assert set(sizes) == set(range(5))
        # Capacity rule: each fold stops accepting once past total/n, so no fold
        # can exceed capacity by more than one (largest) cell.
        assert max(sizes.values()) <= 100 * 100 / 5 + 100

    def test_never_runs_out_of_folds(self):
        # One huge cell fills a fold immediately; remaining cells must still land.
        counts = {"big": 10_000, **{f"c{i}": 1 for i in range(50)}}
        folds = assign_folds(counts, 2, seed=7)
        assert len(folds) == 51
