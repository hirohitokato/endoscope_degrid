# Endoscope Degrid

光ファイバー内視鏡動画のハニカム／格子模様を除去するPythonプロジェクトです。CPUと、Apple SiliconのMetal GPUに対応しています。
標準の `notch` は複数フレームから格子周波数を推定し、その帯域を選択的に除去します。
`core` はファイバー標本の位置と透過率を推定し、不規則な標本から画像を再構成します。
学習済みモデル、生成AI、画像全体のぼかし、モルフォロジーは使用しません。

## デスクトップ版

[Desktop builds](https://github.com/hirohitokato/endoscope_degrid/actions/workflows/desktop.yml) の成功した実行から、お使いの OS の配布物をダウンロードできます。ZIP を展開し、Windows x64 では `Endoscope Degrid.exe`、Apple Silicon Mac では `Endoscope Degrid.app` を開いてください。利用者による Python・Node.js・FFmpeg のインストールは不要です。

画面で動画を選び、先頭10秒または全編の書き出し先を指定します。詳しい展開手順、CLI の場所、対応環境、署名とライセンスについては[配布ガイド](docs/DISTRIBUTION.md)を参照してください。

## ソースから使う

[uv](https://docs.astral.sh/uv/) を用意し、リポジトリのルートで実行します。

```sh
uv sync --locked --managed-python --extra test
uv run --locked --extra test pytest -q
uv run --locked endoscope-degrid-desktop
```

実行環境は `.python-version` と `uv.lock` で管理します。PyAV の FFmpeg ライブラリで、すべての動画をローカル処理します。

## 使い方

最初に静止画と検出した周波数を確認できます。

```sh
uv run --locked endoscope-degrid analyze /path/to/source.mp4 --report-dir output/inspection
```

10秒間の動画プレビューを生成します。元動画の30秒から始まります。

```sh
uv run --locked endoscope-degrid process /path/to/source.mp4 \
  -o output/preview.mp4 --start 30 --duration 10
```

全編処理は `--start` と `--duration` を省略します。

```sh
uv run --locked endoscope-degrid process /path/to/source.mp4 -o output/source_degrid.mp4
```

`core` を選ぶ場合、および両方式の静止画を比較する場合:

```sh
uv run --locked endoscope-degrid process /path/to/source.mp4 \
  -o output/source_core.mp4 --method core --start 30 --duration 10
uv run --locked endoscope-degrid compare /path/to/source.mp4 --report-dir output/method_comparison
```

複数動画は逐次処理します。各動画で別々に校正します。
出力名には親フォルダ名と入力パスの短いハッシュを含めるため、同じ `source.mp4` という名前でも衝突しません。
一つの動画が失敗しても残りを処理し、最後に非ゼロ終了コードを返します。

```sh
uv run --locked endoscope-degrid batch \
  /path/to/first/source.mp4 /path/to/second/source.mp4 -o output/batch
```

## 入出力の扱い

- 対応: MP4/MOV、映像ストリーム1本、8-bit H.264、音声0本以上。
- 解像度、画素アスペクト比、各フレームの表示時刻、フレーム数、プログレッシブ／インターレースとフィールド順を維持します。
- インターレースは二つのフィールドを独立して処理し、元の順で再符号化します。解除や59.94pへの変換はしません。
- 入力の `average_rate` や `nb_frames` がフィールド数を表すことがあります。実際にデコードした時刻を使用し、動画を倍速・半速にしません。
- 音声は再圧縮せずコピーします。全編出力では音声パケットの内容と時刻をSHA-256で検証します。
- 短区間の音声は圧縮パケット単位で切り出すため、映像の切り出し境界と最大1音声パケット程度ずれることがあります。レポートの音声検証は選択したパケットのコピーが正確であることを意味します。
- 映像はH.264に再圧縮します。標準は `CRF 18 / preset medium`。ビットレート、ファイルサイズ、エンコーダ、H.264内部のフィールド／フレームの符号化方式は元と同一にはなりません。
- CFRもVFRも表示時刻を保持します。VFRの名目fps表記より各フレームのPTSを優先します。
- 色空間・色域等の宣言を引き継ぎます。色変換と再圧縮による画素値の変化はあります。高ビット深度、H.265、字幕・データストリーム、途中で解像度／走査方式が変わる動画は理由を表示して停止します。
- 元動画を上書きしません。既存の出力は `--overwrite` を明示した場合のみ置き換えます。キャンセル・失敗時の未完成動画は削除し、検証済みの動画だけを出力名に確定します。

## 画質の調整

| オプション | 標準値 | 意味 |
|---|---:|---|
| `--method` | `notch` | `notch` / `core` |
| `--strength` | `1.0` | 補正量。0は処理前と同じ、1は全補正。0〜1 |
| `--bandwidth` | `0.18` | 基本格子周波数に対するノッチ半径。0.05〜0.30 |
| `--samples` | `24` | 全編に均等配置した校正フレーム数。4〜128 |
| `--peak-db` | `12` | 同じ半径のスペクトル中央値に対するピーク検出閾値 |
| `--max-peaks` | `100` | 検出ピークの上限 |
| `--min-frequency` | `0.025` | 最小候補周波数（元画像の1画素あたりの周期数） |
| `--roi X Y W H` | 自動 | 処理範囲。出力画像は切り抜かず元の大きさを維持 |
| `--workers` | `4` | 1フレームあたりのFFTのCPUスレッド数 |
| `--frame-workers` | `1` | 同時処理するフレーム数（1〜8、複数指定は `notch` 用） |
| `--backend` | `auto` | `notch` を `cpu` または `metal` で実行 |
| `--crf` | `18` | 出力圧縮量。小さいほど高画質・大容量 |
| `--preset` | `medium` | H.264エンコードの速度と圧縮効率 |

格子残りが強ければ、短区間で `--bandwidth 0.22` などを比較してください。
帯域を広げるほど同じ周波数にある被写体の情報も失われます。`--strength` を下げると格子も戻ります。
`core` は格子を目立ちにくくしますが、細部がなめらかになり、標本の推定誤差による質感・明るさの変化が出ることがあります。
どちらの方式も、白飛び、ファイバー間の未観測情報、元動画にない細部を正しく復元できると保証するものではありません。

## 高速な書き出し

M2で検証した1080pの `notch` 出力では、フレーム並列処理と高速プリセットを次のように組み合わせられます。

```sh
uv run --locked endoscope-degrid process /path/to/source.mp4 -o output/fast.mp4 \
  --backend cpu --preset ultrafast --crf 16 --workers 2 --frame-workers 2
```

この設定では元の解像度、表示時刻、フィールド順、音声を維持します。
`ultrafast` と低いCRFの組み合わせは、既定設定に比べて出力ファイルが大きくなる傾向があります。

Apple Silicon Mac（macOS 14以降）では、オプション依存を追加すると格子除去をMetalで実行できます。

```sh
uv run --locked --extra gpu endoscope-degrid process /path/to/source.mp4 -o output/metal.mp4 \
  --backend metal --preset ultrafast --crf 16 --workers 2 --frame-workers 2
```

`--backend auto` はMetalを利用できる `notch` 処理でGPUを選び、他の環境や `core` ではCPUを選びます。
解像度、表示時刻、フィールド順、音声は同じ検証を通ります。

## レポート

標準では出力動画と同じ場所に `<出力名>_report/` を作ります。

- `report.json`: 入力ハッシュ、動画仕様、全設定、検出ピーク、処理状況、出力ハッシュ、時刻・音声の検証結果。
- `comparison_*.png` / `detail_*.png`: 元画像と処理後の全体／等倍比較。
- `spectrum_mask_field_*.png`: 左が観測スペクトル、右が適用マスク。黒い場所を減衰。
- `calibration.npz`: 実際のスペクトルとマスク。別動画に無検証で使い回さないため、標準CLIでは動画ごとに再計算。

格子帯域のエネルギー低減dBは除去量の指標です。解剖学的な忠実性、解像度向上、正解画像との一致を示す指標ではありません。
レポートの静止画は校正にも使用したフレームです。別時刻での検証結果は `docs/VALIDATION.md` を参照してください。

手法の根拠と原論文からの変更点は [docs/METHODS.md](docs/METHODS.md)、実装構成は [docs/DESIGN.md](docs/DESIGN.md) に記載しています。
