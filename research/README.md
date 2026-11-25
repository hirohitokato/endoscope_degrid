# 方式選択時の比較実験

`results/` は方式選択に使用した2動画の4列比較です。左から原画像、A（周波数ノッチ）、B（透過率推定＋TV反復復元）、C（コア標本再構成）。
これは製品化前の固定ROI・NumPy FFTを使った試作の結果です。本番実装はROI自動検出、SciPy FFT、パディング等を追加しています。

当時の試作コードも保存しています。入力パスは指定された2本をコード中に明記しています。再実行すると約300MBの標本配列を各動画について `results/` に保存します。

```sh
.venv/bin/python research/compare_methods.py
.venv/bin/python research/compare_core.py
```

`comparison_timing.json` と `core_timing.json` はその試作での画像演算時間です。デコード・エンコード時間を含まず、本番実装の速度を示すものではありません。
本番CLIでは `degrid.py compare INPUT --report-dir DIR` でA/Cを再比較できます。
校正画像なしのBを原論文のTwISTそのものと解釈しないでください。数式・変更点は `docs/METHODS.md` に記載しています。
