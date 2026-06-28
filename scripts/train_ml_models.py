"""Train AURUM-1 ML models on 11-year dataset. Saves artifacts for D6 testing."""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.features.engineer import FeatureEngineer
from aurum1.models.regime_classifier import RegimeClassifier
from aurum1.models.direction_predictor import DirectionPredictor

def main():
    root = Path('/opt/aurum1')
    settings = load_settings(root / 'aurum1' / 'config' / 'settings.yaml')
    ohlcv = load_ohlcv('M15', root / 'aurum1' / 'data' / 'backtest_market_cache.sqlite3')
    print(f'Loaded {len(ohlcv)} M15 candles ({ohlcv.index[0].date()} to {ohlcv.index[-1].date()})')

    dates = ohlcv.index.normalize().unique()
    macro = pd.DataFrame({'dgs10': 4.0, 'cpi': 300.0, 'cpi_yoy': 3.0, 'real_yield': 1.0,
        'dxy': 100.0, 'dxy_daily_return': 0.0, 'vix': 20.0, 'vix_1d_change': 0.0},
        index=pd.DatetimeIndex(dates, name='date'))
    cot = pd.DataFrame({'market_name': ['GOLD'], 'open_interest': [1.0], 'long_positions': [0.0],
        'short_positions': [0.0], 'net_positioning': [0.0], 'cot_net_long_pct': [0.0], 'source': ['placeholder']},
        index=pd.DatetimeIndex([dates[0]], name='report_date'))

    print('Building features...')
    engineer = FeatureEngineer({'feature_engineering': {'lookahead_check': False}})
    features = engineer.build_features(ohlcv, macro, cot, include_target=True)
    print(f'Features: {features.shape[0]} rows, {features.shape[1]} cols')
    print(f'Label distribution: {features["label"].value_counts().to_dict()}')

    print('\nTraining RegimeClassifier (full data, internal rolling window)...')
    rc = RegimeClassifier(settings)
    rc_meta = rc.train(features, update_latest=True)
    print(f'  Validation F1: {rc_meta.get("validation_f1", "N/A")}')
    print(f'  Validation Sharpe: {rc_meta.get("validation_sharpe", "N/A")}')

    # DirectionPredictor needs sequences - train on last 2 years to avoid OOM
    print('\nTraining DirectionPredictor (last 48K rows)...')
    train_window = features.iloc[-48000:].copy()
    print(f'  Training rows: {len(train_window)}')
    dp = DirectionPredictor(settings)
    dp_meta = dp.train(train_window, update_latest=True)
    print(f'  Directional accuracy: {dp_meta.get("directional_accuracy", "N/A")}')
    print(f'  Validation Sharpe: {dp_meta.get("validation_sharpe", "N/A")}')
    print(f'  Decision: {dp_meta.get("decision", "N/A")}')

    from aurum1.models.utils import model_dir_from_settings
    model_dir = model_dir_from_settings(settings)
    print(f'\nModel artifacts directory: {model_dir}')
    latest_files = list(model_dir.glob('*_latest.pkl'))
    for f in latest_files:
        print(f'  {f.name} ({f.stat().st_size / 1024 / 1024:.2f} MB)')
    print(f'Total artifacts: {len(latest_files)}')

    summary = {'candles': len(ohlcv), 'feature_rows': features.shape[0],
        'label_dist': features['label'].value_counts().to_dict(),
        'regime_classifier': {'validation_f1': rc_meta.get('validation_f1'),
            'validation_sharpe': rc_meta.get('validation_sharpe')},
        'direction_predictor': {'directional_accuracy': dp_meta.get('directional_accuracy'),
            'validation_sharpe': dp_meta.get('validation_sharpe'),
            'decision': dp_meta.get('decision')},
        'artifacts': [f.name for f in latest_files]}
    report_path = root / 'reports' / 'forward_shadow' / 'ml_training_report.json'
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, indent=2, default=str))
    print(f'\nReport: {report_path}')

if __name__ == '__main__':
    main()
