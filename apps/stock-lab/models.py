"""Win-probability models for the Coach.

Three learners are trained on what was known at the moment a signal fired (levels and slopes of price, VWAP,
volume and the index) to predict whether the trade wins under the plan that is actually executed:

  logit     logistic regression            — linear, the most stable on small samples
  gbm       gradient-boosted trees         — picks up thresholds and interactions
  dnn       deep neural network (3 layers) — averaged over several random starts
  ensemble  the average of the three

Everything is evaluated walk-forward: a model that scores day d has only seen days before d.
"""
import warnings

import numpy as np

FEATURES = ["mins", "gap", "relvol", "or_pct", "stop_pct", "stop_atr", "vwap_dist", "slope_px", "slope_px_long",
            "slope_accel", "slope_vwap", "slope_vol", "day_pos", "day_move", "last_bar", "nifty_move", "nifty_slope",
            "rel_strength", "mood_bias", "vix", "vix_chg", "is_orb", "is_long"]
FEATURE_LABEL = {
    "mins": "Later in the session", "gap": "Opening gap in the trade's direction", "relvol": "Higher relative volume",
    "or_pct": "Wider opening range", "stop_pct": "Stop further away (%)", "stop_atr": "Stop further away (in normal bar sizes)",
    "vwap_dist": "Entry further beyond VWAP", "slope_px": "Price slope, last 30 min, in the trade's direction",
    "slope_px_long": "Price slope, last hour, in the trade's direction", "slope_accel": "Price slope accelerating",
    "slope_vwap": "VWAP slope in the trade's direction", "slope_vol": "Volume rising into the signal",
    "day_pos": "Entry near the day's extreme in the trade's direction", "day_move": "Stock already moved in the trade's direction",
    "last_bar": "Last bar closed strongly in the trade's direction", "nifty_move": "Nifty moving in the trade's direction",
    "nifty_slope": "Nifty slope, last 30 min, in the trade's direction", "rel_strength": "Stock stronger than Nifty",
    "mood_bias": "Market mood agrees with the trade", "vix": "Higher India VIX", "vix_chg": "VIX rising vs previous day",
    "is_orb": "Opening-range breakout (vs VWAP pullback)", "is_long": "Long (vs short)"}
KINDS = {"logit": "Logistic regression", "gbm": "Gradient-boosted trees", "dnn": "Deep neural network",
         "ensemble": "Ensemble (average of the three)"}
BASE = ["logit", "gbm", "dnn"]
MIN_TRAIN = 60


def matrix(xs):
    """List of feature dicts (or None) → float matrix with NaN where a value is missing."""
    X = np.full((len(xs), len(FEATURES)), np.nan)
    for i, x in enumerate(xs):
        if isinstance(x, dict):
            for j, f in enumerate(FEATURES):
                v = x.get(f)
                if v is not None:
                    try:
                        X[i, j] = float(v)
                    except (TypeError, ValueError):
                        pass
    X[~np.isfinite(X)] = np.nan
    return X


def _members(kind, seeds):
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.neural_network import MLPClassifier
    if kind == "logit":
        return [LogisticRegression(C=0.3, max_iter=1000)]
    if kind == "gbm":
        return [HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=80, min_samples_leaf=20,
                                               l2_regularization=1.0, random_state=0)]
    if kind == "dnn":
        return [MLPClassifier(hidden_layer_sizes=(32, 16, 8), alpha=0.05, learning_rate_init=0.01, max_iter=250,
                              random_state=s) for s in range(seeds)]
    raise ValueError(kind)


class Brain:
    """One fitted model (or the ensemble). predict() returns win probability and an expected R derived from it."""

    def __init__(self, kind, seeds=3):
        self.kind, self.seeds, self.ok, self.n = kind, seeds, False, 0
        self.p0 = self.r0 = self.avg_win = self.avg_loss = None
        self.parts = {}

    def fit(self, X, y, r):
        X, y, r = np.asarray(X, float), np.asarray(y, int), np.asarray(r, float)
        self.n = len(y)
        if self.n < MIN_TRAIN or y.sum() < 10 or (1 - y).sum() < 10 or np.isnan(X).all():
            return self
        self.p0, self.r0 = float(y.mean()), float(r.mean())
        self.avg_win, self.avg_loss = float(r[y == 1].mean()), float(r[y == 0].mean())
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.med = np.where(np.isnan(X).all(axis=0), 0.0, np.nanmedian(X, axis=0))
            Xi = np.where(np.isnan(X), self.med, X)
            self.mu, self.sd = Xi.mean(axis=0), Xi.std(axis=0)
            self.sd[self.sd < 1e-9] = 1.0
            Z = self._z(Xi)
            for k in (BASE if self.kind == "ensemble" else [self.kind]):
                self.parts[k] = [m.fit(Z, y) for m in _members(k, self.seeds)]
        self.ok = True
        return self

    def _z(self, Xi):
        return np.clip((Xi - self.mu) / self.sd, -4, 4)

    def proba(self, X, by_part=False):
        X = np.asarray(X, float)
        Z = self._z(np.where(np.isnan(X), self.med, X))
        out = {k: np.mean([m.predict_proba(Z)[:, 1] for m in ms], axis=0) for k, ms in self.parts.items()}
        return out if by_part else np.mean(list(out.values()), axis=0)

    def exp_r(self, p):
        """Expected R if wins and losses keep their recent average size."""
        return p * self.avg_win + (1 - p) * self.avg_loss

    def predict(self, x):
        if not self.ok:
            return None, None
        p = float(self.proba(matrix([x]))[0])
        return p, float(self.exp_r(p))

    def leanings(self):
        """Standardised logistic-regression weights: which inputs push win odds up (+) or down (−)."""
        ms = self.parts.get("logit")
        if not ms:
            return []
        c = ms[0].coef_[0]
        return sorted([(float(c[j]), f, FEATURE_LABEL[f]) for j, f in enumerate(FEATURES)], key=lambda t: -abs(t[0]))


def walk_forward(train, tests, test_days, warmup=10, step=3, seeds=3):
    """Out-of-sample win probability and expected R from every model.
    train = (X, y, r, days) — the study set the models learn from.
    tests = {name: (X, days)} — sets to score. Days in `test_days[warmup:]` are scored in blocks of `step`
    by models fitted only on study rows from earlier days.
    Returns {name: {kind: (p, exp_r)}} with NaN where there was no model yet."""
    X, y, r, days = (np.asarray(v) for v in train)
    X, y, r = X.astype(float), y.astype(int), r.astype(float)
    uniq = sorted(set(test_days))
    out = {nm: {k: (np.full(len(d), np.nan), np.full(len(d), np.nan)) for k in KINDS} for nm, (_, d) in tests.items()}
    for s in range(warmup, len(uniq), step):
        block = uniq[s:s + step]
        tr = days < block[0]
        b = Brain("ensemble", seeds).fit(X[tr], y[tr], r[tr])
        if not b.ok:
            continue
        for nm, (Xt, dt) in tests.items():
            te = np.isin(np.asarray(dt), block)
            if not te.any():
                continue
            parts = b.proba(np.asarray(Xt, float)[te], by_part=True)
            parts["ensemble"] = np.mean([parts[k] for k in BASE], axis=0)
            for k, pr in parts.items():
                out[nm][k][0][te], out[nm][k][1][te] = pr, b.exp_r(pr)
    return out


def auc(p, y):
    """Chance that a random winner was scored above a random loser (0.5 = no skill)."""
    p, y = np.asarray(p, float), np.asarray(y, int)
    n1, n0 = int(y.sum()), int((1 - y).sum())
    if not n1 or not n0:
        return np.nan
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p))
    ranks[order] = np.arange(1, len(p) + 1)
    for v in np.unique(p):                       # average ranks for ties
        m = p == v
        if m.sum() > 1:
            ranks[m] = ranks[m].mean()
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))
