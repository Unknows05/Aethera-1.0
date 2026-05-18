"""
ML Engine — XGBoost signal quality filter with concept drift monitoring.
Walk-forward TimeSeriesSplit. Independent from scorer (no circular features).
"""
import json
import logging
import os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import xgboost as xgb
    from sklearn.model_selection import TimeSeriesSplit
    HAS_XGBOOST = True
except ImportError:
    HAS_XGBOOST = False
    logger.warning("[ML] xgboost/scikit-learn not installed")

try:
    import joblib
    HAS_JOBLIB = True
except ImportError:
    HAS_JOBLIB = False


class MLEngine:
    """
    20 features — independent from scorer output (no confluence_score).
    Concept drift: tracked via live WR vs shadow WR.
    Threshold: bidirectional adjustment (up on good WR, down on 3-week decline).
    """

    FEATURES = [
        "oi_change_1h", "oi_change_4h", "oi_change_24h",
        "funding_rate",
        "ls_ratio_retail", "ls_ratio_top_trader", "taker_buy_ratio",
        "rsi_1h", "rsi_4h",
        "macd_histogram_4h", "macd_cross",
        "ema9_vs_ema21", "adx_4h", "bb_position", "atr_pct",
        "volume_zscore_1h", "volume_zscore_4h", "candle_aggression",
        "btc_trend_4h",
        "hours_since_last_signal",
    ]

    MIN_SAMPLES = 50
    MODEL_FILE = "data/model_xgb_latest.pkl"
    META_FILE = "data/model_xgb_meta.json"

    def __init__(self, db_path: str = "data/screener.db"):
        self.db_path = db_path
        self.model: Optional[xgb.XGBClassifier] = None
        self.feature_importance: Dict[str, float] = {}
        self.threshold: float = 0.55
        self.shadow_mode = True
        self.min_samples_for_live = 300
        self.trained_at = ""
        self.samples = 0
        self.wr_out_of_sample = 0.0
        self.status = "untrained"
        self.deployed = False
        self.version = ""
        self.top_features = ""
        # Concept drift tracking
        self._live_wr_history: list = []
        self._shadow_wr_history: list = []
        self._drift_detected = False
        self._load_model()

    def _load_model(self):
        if not HAS_XGBOOST: return
        if os.path.exists(self.META_FILE):
            try:
                with open(self.META_FILE) as f:
                    data = json.load(f)
                self.threshold = data.get("threshold", 0.55)
                self.shadow_mode = data.get("shadow_mode", True)
                self.trained_at = data.get("trained_at", "")
                self.samples = data.get("samples", 0)
                self.wr_out_of_sample = data.get("wr_out_of_sample", 0)
                self.status = data.get("status", "untrained")
                self.deployed = data.get("deployed", False)
                self.version = data.get("version", "")
                self.top_features = data.get("top_features", "")
                self.feature_importance = data.get("feature_importance", {})
                self._live_wr_history = data.get("live_wr_history", [])
                self._shadow_wr_history = data.get("shadow_wr_history", [])
            except Exception as e:
                logger.warning(f"[ML] Meta load error: {e}")
        if os.path.exists(self.MODEL_FILE) and HAS_JOBLIB:
            try:
                self.model = joblib.load(self.MODEL_FILE)
            except Exception as e:
                logger.warning(f"[ML] Model load error: {e}")

    def _save_model(self):
        if self.model is None or not HAS_JOBLIB: return
        try:
            Path(self.MODEL_FILE).parent.mkdir(parents=True, exist_ok=True)
            joblib.dump(self.model, self.MODEL_FILE)
            fi = {k: float(v) if hasattr(v, 'item') else v for k, v in self.feature_importance.items()}
            meta = {
                "version": self.version, "trained_at": self.trained_at,
                "samples": int(self.samples), "wr_out_of_sample": float(self.wr_out_of_sample),
                "threshold": float(self.threshold), "feature_importance": fi,
                "top_features": self.top_features, "status": self.status,
                "deployed": self.deployed, "shadow_mode": self.shadow_mode,
                "live_wr_history": self._live_wr_history[-20:],
                "shadow_wr_history": self._shadow_wr_history[-20:],
            }
            with open(self.META_FILE, "w") as f:
                json.dump(meta, f, indent=2)
        except Exception as e:
            logger.error(f"[ML] Save error: {e}")

    def is_ready(self) -> bool:
        return self.model is not None and HAS_XGBOOST

    def check_and_update_mode(self, labeled_count: int):
        if self.shadow_mode and labeled_count >= self.min_samples_for_live:
            self.shadow_mode = False
            self.threshold = max(self.threshold, 0.55)
            self._save_model()
            logger.warning(f"[ML] LIVE MODE: {labeled_count} samples")

    def record_outcome(self, prob_win: float, actual_win: bool):
        """Track live/shadow WR for concept drift detection."""
        if self.shadow_mode:
            self._shadow_wr_history.append(1.0 if actual_win else 0.0)
        else:
            self._live_wr_history.append(1.0 if actual_win else 0.0)

    def check_concept_drift(self) -> bool:
        """Detect if live WR has diverged significantly from training WR."""
        if len(self._live_wr_history) < 20: return False
        recent_wr = sum(self._live_wr_history[-20:]) / 20
        if self.wr_out_of_sample > 0 and recent_wr < (self.wr_out_of_sample / 100 - 0.15):
            self._drift_detected = True
            logger.warning(f"[ML] Concept drift: live WR {recent_wr:.1%} << train WR {self.wr_out_of_sample/100:.1%}")
            return True
        self._drift_detected = False
        return False

    def filter_signal(self, features: Dict) -> Dict:
        if not self.is_ready():
            return {"pass": True, "confidence": 50, "threshold_used": 55, "reason": "model not ready"}
        if self._drift_detected:
            return {"pass": True, "confidence": 50, "threshold_used": 55, "reason": "concept drift — fallback to rules"}
        try:
            feat_dict = {f: features.get(f, 0) for f in self.FEATURES}
            X = pd.DataFrame([feat_dict])[self.FEATURES].fillna(0)
            prob_win = float(self.model.predict_proba(X)[0][1])
            conf = round(prob_win * 100, 1)
            return {"pass": prob_win >= self.threshold, "confidence": conf,
                    "threshold_used": round(self.threshold * 100, 1), "reason": "ML prediction"}
        except Exception as e:
            logger.error(f"[ML] filter_signal error: {e}")
            return {"pass": True, "confidence": 50, "threshold_used": 55, "reason": f"error: {e}"}

    def filter_signal_shadow(self, features: Dict, symbol: str = None) -> Dict:
        if not self.is_ready():
            return {"pass": True, "confidence": 50, "shadow": True, "reason": "model not ready"}
        try:
            feat_dict = {f: features.get(f, 0) for f in self.FEATURES}
            X = pd.DataFrame([feat_dict])[self.FEATURES].fillna(0)
            prob = float(self.model.predict_proba(X)[0][1])
            if self.shadow_mode:
                try:
                    from src.database import ScreenerDB
                    db = ScreenerDB(self.db_path)
                    db.save_ml_shadow(symbol or '', 0, prob, self.version)
                except Exception:
                    pass
                return {"pass": True, "confidence": 50, "shadow": True,
                        "shadow_prob": round(prob * 100, 1)}
            return {"pass": prob >= self.threshold, "confidence": round(prob * 100, 1),
                    "threshold_used": round(self.threshold * 100, 1),
                    "shadow": False, "reason": "ML live"}
        except Exception as e:
            logger.error(f"[ML] filter_signal_shadow error: {e}")
            return {"pass": True, "confidence": 50, "shadow": True, "reason": f"error: {e}"}

    def load_training_data(self, lookback_days: int = 60) -> pd.DataFrame:
        try:
            from src.database import ScreenerDB
            db = ScreenerDB(self.db_path)
            rows = db.get_training_data(lookback_days)
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame(rows)
            available = [f for f in self.FEATURES if f in df.columns]
            if "confluence_score" in df.columns:
                available = [f for f in available if f != "confluence_score"]
            df = df[available + ["signal_result"]].dropna(subset=available)
            df["label"] = (df["signal_result"] == "WIN").astype(int)
            return df
        except Exception as e:
            logger.error(f"[ML] load_training_data error: {e}")
            return pd.DataFrame()

    def train_with_walkforward(self, lookback_days: int = 60) -> Dict:
        if not HAS_XGBOOST:
            return {"status": "no_xgboost", "reason": "xgboost not installed"}
        df = self.load_training_data(lookback_days)
        if len(df) < self.MIN_SAMPLES:
            return {"status": "insufficient_data", "samples": len(df), "min_required": self.MIN_SAMPLES}
        available = [f for f in self.FEATURES if f in df.columns]
        if len(available) < 10:
            return {"status": "insufficient_features", "available": len(available)}
        X = df[available].values
        y = df["label"].values
        n_folds = min(5, max(2, len(df) // 20))
        tscv = TimeSeriesSplit(n_splits=n_folds)
        wr_folds = []
        for train_idx, val_idx in tscv.split(X):
            X_train, X_val = X[train_idx], X[val_idx]
            y_train, y_val = y[train_idx], y[val_idx]
            pos_weight = max(1, (len(y_train) - y_train.sum()) / max(1, y_train.sum()))
            model = xgb.XGBClassifier(
                n_estimators=min(200, len(y_train) * 2), max_depth=4,
                learning_rate=0.05, subsample=0.8, colsample_bytree=0.7,
                min_child_weight=5, reg_alpha=0.1, reg_lambda=1.0,
                scale_pos_weight=pos_weight, eval_metric="logloss",
                early_stopping_rounds=20, random_state=42, n_jobs=-1, verbosity=0,
            )
            model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
            y_prob = model.predict_proba(X_val)[:, 1]
            y_pred = (y_prob >= self.threshold).astype(int)
            wr_folds.append(y_val[y_pred == 1].mean() if y_pred.sum() > 0 else 0.0)
        avg_wr = np.mean(wr_folds) if wr_folds else 0.0
        if avg_wr >= 0.50:
            pos_weight = max(1, (len(y) - y.sum()) / max(1, y.sum()))
            self.model = xgb.XGBClassifier(
                n_estimators=min(200, len(y) * 2), max_depth=4,
                learning_rate=0.05, subsample=0.8, colsample_bytree=0.7,
                min_child_weight=5, reg_alpha=0.1, reg_lambda=1.0,
                scale_pos_weight=pos_weight, eval_metric="logloss",
                early_stopping_rounds=20, random_state=42, n_jobs=-1, verbosity=0,
            )
            self.model.fit(X, y, eval_set=[(X, y)], verbose=False)
            self.feature_importance = dict(zip(available, self.model.feature_importances_))
            sorted_fi = sorted(self.feature_importance.items(), key=lambda x: x[1], reverse=True)
            self.top_features = ", ".join([f"{n}:{v:.3f}" for n, v in sorted_fi[:5]])
            # Bidirectional threshold adjustment
            if avg_wr >= 0.60:
                self.threshold = min(0.70, self.threshold + 0.02)
            elif avg_wr < 0.50 and self.threshold > 0.50:
                self.threshold = max(0.50, self.threshold - 0.02)
            self.version = datetime.now().strftime("%Y%m%d-%H%M")
            self.trained_at = datetime.now().isoformat()
            self.samples = len(df)
            self.wr_out_of_sample = round(avg_wr * 100, 1)
            self.status = "deployed"
            self.deployed = True
            self._save_model()
            logger.info(f"[ML] Deployed: WR={avg_wr:.1%}, threshold={self.threshold}, samples={len(df)}")
            return {"status": "deployed", "wr_out_of_sample": round(avg_wr * 100, 1),
                    "threshold": self.threshold, "samples": len(df),
                    "top_features": self.top_features, "fold_wrs": wr_folds}
        else:
            logger.warning(f"[ML] Rejected: WR={avg_wr:.1%} < 50%")
            return {"status": "rejected", "wr_out_of_sample": round(avg_wr * 100, 1),
                    "reason": f"WR {avg_wr:.1%} below 50%", "fold_wrs": wr_folds}

    def get_shadow_status(self) -> dict:
        try:
            from src.database import ScreenerDB
            db = ScreenerDB(self.db_path)
            labeled = db.get_ml_shadow_count()
        except Exception:
            labeled = 0
        return {
            "shadow_mode": self.shadow_mode, "labeled_samples": labeled,
            "samples_needed": max(0, self.min_samples_for_live - labeled),
            "live_threshold": self.threshold,
            "drift_detected": self._drift_detected,
        }

    def get_status(self) -> Dict:
        return {
            "trained": self.is_ready(), "threshold": self.threshold,
            "samples": self.samples, "wr_out_of_sample": self.wr_out_of_sample,
            "status": self.status, "trained_at": self.trained_at,
            "top_features": self.top_features, "version": self.version,
            "shadow_mode": self.shadow_mode, "drift_detected": self._drift_detected,
        }

    def get_feature_importance_report(self) -> Dict:
        return dict(sorted(self.feature_importance.items(), key=lambda x: x[1], reverse=True))

    def get_position_size(self, ml_confidence: float, regime: str) -> Tuple[float, str]:
        conf = ml_confidence / 100.0 if ml_confidence > 1 else ml_confidence
        if conf >= 0.70: size = min(1.0, 0.70 + (conf - 0.70) * 3.0)
        elif conf >= 0.60: size = conf / 0.72
        elif conf >= 0.50: size = max(0.40, conf / 0.65)
        else: size = max(0.20, conf / 0.50)
        return round(size, 2), f"ml:{conf:.1%}({regime})"


_ml_engine: Optional[MLEngine] = None


def get_ml_engine(db_path: str = "data/screener.db") -> MLEngine:
    global _ml_engine
    if _ml_engine is None:
        _ml_engine = MLEngine(db_path)
    return _ml_engine
