# デスクトップ版

Windows x64 と Apple Silicon Mac 用のアプリを各 OS 上でビルドします。Actions の Desktop builds を開き、成功した実行の Artifacts で対象 OS を選んでください。ダウンロードしたファイルを展開し、中の `Endoscope-Degrid-*.zip` も展開します。Mac は Finder のアーカイブユーティリティで開くとアプリのリンクと実行権限が保持されます。

- **Windows** — 展開したフォルダの `Endoscope Degrid.exe` を起動します。コマンド操作には隣の `endoscope-degrid-cli.exe` を使えます。移動する場合はフォルダ全体を移動してください。
- **macOS** — `Endoscope Degrid.app` を開きます。CLI は `"./Endoscope Degrid.app/Contents/MacOS/Endoscope Degrid" --help` で確認できます。Apple Silicon の Metal を利用できる場合、標準の notch 処理で自動選択します。
- 「動画を選ぶ…」で元動画を選び、「先頭10秒を書き出す…」または「全編を書き出す…」で保存先を指定します。校正には両方とも全編を使います。新しい動画と同じ場所に処理レポートを保存します。

## 開発者向けビルド

リポジトリのルートで次を実行します。ビルドには uv 管理の CPython 3.13 と Tcl/Tk を使います。

```sh
uv sync --locked --managed-python --group packaging --extra gpu --extra test
uv run --no-sync python -m pytest -q
uv run --no-sync python packaging/build.py
```

`dist/` に対象 OS の ZIP ができます。Mac の MLX はビルドに使用した OS に対応する wheel が選ばれるため、配布先の最古のバージョンでビルド・検証してください。CI の対象は Windows x64 と macOS 15 arm64 です。CI が生成する成果物には、開発者証明書による Windows 署名や Apple の Developer ID 署名・公証がありません。組織の端末では配布元の承認が必要な場合があります。

## 同梱ソフトウェア

配布物の `licenses/`（Windows は `_internal/` 内、Mac はアプリ内の `Contents/Resources/`）に主要な wheel のライセンスを収録します。Python・Tcl/Tk、NumPy、SciPy、OpenCV、PyAV、PyInstaller のブートローダー、および Mac 用 MLX を使用します。数値ライブラリとコーデックにはさらに外部ライブラリが含まれます。

PyAV 16.0.1 の FFmpeg と H.264 エンコーダ libx264 を同梱します。再配布時には使用したバイナリに対応するソース提供などの条件を確認してください。開発者向けの参照先は [PyAV 16.0.1](https://github.com/PyAV-Org/PyAV/tree/v16.0.1)、[FFmpeg ビルド定義](https://github.com/PyAV-Org/pyav-ffmpeg)、[FFmpeg のライセンス](https://ffmpeg.org/legal.html)、[x264](https://www.videolan.org/developers/x264.html) です。
