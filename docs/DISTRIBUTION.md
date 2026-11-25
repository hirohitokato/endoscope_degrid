# デスクトップ版

Windows x64 と Apple Silicon Mac 用のアプリを各 OS 上でビルドします。Actions の Desktop builds を開き、成功した実行の Artifacts で対象 OS を選んでください。ダウンロードしたファイルを展開し、中のアプリ用 ZIP（`Endoscope-Degrid-windows-x64.zip` または `Endoscope-Degrid-macos-arm64.zip`）を展開します。Mac は Finder のアーカイブユーティリティで開くとアプリのリンクと実行権限が保持されます。

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

### 手元で ZIP を検証する

リポジトリのルートで、macOS は次を実行します。展開したアプリのバージョン、GUI 起動、合成動画の書き出しと検証レポートを確認します。ウィンドウの検出には Swift を利用します。

```sh
DEGRID_ARCHIVE=dist/Endoscope-Degrid-macos-arm64.zip uv run --no-sync python -m pytest -q tests/test_desktop_archive.py
```

Windows は PowerShell から実行します。展開先の GUI と CLI を両方検証します。

```powershell
$env:DEGRID_ARCHIVE = 'dist/Endoscope-Degrid-windows-x64.zip'
uv run --no-sync python -m pytest -q tests/test_desktop_archive.py
Remove-Item Env:DEGRID_ARCHIVE
```

## 同梱ソフトウェア

配布物の `licenses/`（Windows は `_internal/` 内、Mac はアプリ内の `Contents/Resources/`）に主要な wheel のライセンスを収録します。Python・Tcl/Tk、NumPy、SciPy、OpenCV、PyAV、PyInstaller のブートローダー、および Mac 用 MLX を使用します。数値ライブラリとコーデックにはさらに外部ライブラリが含まれます。

PyAV 16.0.1 の FFmpeg と H.264 エンコーダ libx264 を同梱します。PyAV wheel は pyav-ffmpeg 8.0-2、OpenCV の macOS wheel は別の Homebrew FFmpeg 7.1.1、Windows wheel は専用の動画プラグインを使用します。配布時は、アプリと同じ場所に対応する `-codec-sources.zip` を置いてください。ソース一式の生成例は以下の通りです。

```sh
uv run --no-sync python packaging/codec_sources.py macos-arm64
uv run --no-sync python packaging/codec_sources.py windows-x64
```

このコマンドは固定された版のソースとビルドレシピ・パッチを取得し、SHA-256 を照合します。取得済みのファイルがある場合は `--from-dir DIR` でオフライン生成できます。ZIP 内の `codec-sources.toml` に取得元とハッシュがあります。対象は PyAV の FFmpeg・x264・x265 と、OpenCV が使用する OS 別の FFmpeg・エンコーダーまたは動画プラグインです。その他の同梱ライブラリとアプリ本体については配布形態に応じてライセンス条件を別途確認してください。

上流の参照先は [PyAV 16.0.1](https://github.com/PyAV-Org/PyAV/tree/v16.0.1)、[FFmpeg ビルド定義](https://github.com/PyAV-Org/pyav-ffmpeg)、[FFmpeg のライセンス](https://ffmpeg.org/legal.html)、[x264](https://www.videolan.org/developers/x264.html) です。
