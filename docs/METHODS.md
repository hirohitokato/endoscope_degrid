# 手法と根拠

調査日: 2026-11-06。ユーザー指定により、学習モデルによる細部生成、画像のぼかし、モルフォロジーによる格子除去は採用していません。

## A: 時間方向に校正した周波数選択除去（標準）

根拠: Eimen et al., “Fiberscopic pattern removal for optimal coverage in 3D bladder reconstructions of fiberscope cystoscopy videos”, Journal of Medical Imaging 11(3), 034002 (2024).
DOI: [10.1117/1.JMI.11.3.034002](https://doi.org/10.1117/1.JMI.11.3.034002), [本文](https://pmc.ncbi.nlm.nih.gov/articles/PMC11099938/)。

採用するのは、光ファイバー格子の周波数ピークを検出し、その周囲をノッチで選択的に除去する原理です。
原論文のコードの転載や忠実な再現ではありません。原論文にあるモルフォロジーやマスクのGaussian平滑化は実装していません。

本実装:

1. 動画全体から既定24枚を決定的に抽出。インターレースでは走査線の偶奇を分離。
2. 視野の周囲を含むROIを時間中央値の明るさの占有率で検出。演算時は反射パディングを使用。
3. 校正フレームごとにHann窓を掛けてFFTパワーを計算し、エネルギーで正規化。その時間中央値を使用。
4. 同じ周波数半径のパワー中央値で割り、低周波ほど強い自然画像の傾向を補正。六方向の近似六角形を要求して基本格子周波数を識別。
5. 基本波と高調波等のピークを検出し、幾何学的な距離による重複抑制を適用。
6. 各ピークpに対し、r = 距離(f, p) / ノッチ半径、H_p(f) = r^6 / (1 + r^6)。全ピークのHを乗じる。離散FFTの共役対称性を明示的に保証。
7. DCと基本波の0.45倍未満は通過。0.45〜0.60倍を滑らかに接続。高周波全体を一括除去するローパスではない。
8. 全フレームに固定マスクを使う。毎フレームのマスク変化によるちらつきを避ける。ROI最外周12画素だけ原画像へ滑らかに接続。

Hann窓はスペクトル測定時の端点漏れを抑える重みです。画像のぼかしではありません。
フィールドを別々に処理するため、異なる撮影時刻の上下フィールドの画素を画像復元の演算で混ぜません。
RGBには同じマスクを使用します。これは原論文のフレームごと・色ごとのマスクと異なります。

前提: 格子の方向・間隔が撮影中に大きく変化しないこと。位相のずれには依存しませんが、ズームや光学系の変更には再校正が必要です。
限界: 格子と同じ周波数の被写体成分も失われる。飽和部、周辺の格子ゆがみ、符号化由来の細かい模様には残差があり得る。検出失敗時は自動でぼかしに切り替えず停止する。
格子以外の強い周期的な被写体テクスチャがピークとして選ばれる可能性もあります。自動推定だけで完全な成分分離を保証せず、比較画像と検出マスクを確認できる構成にしています。

## C: ファイバー標本からの再構成（選択可能）

根拠: “Fiber pattern removal and image reconstruction method for snapshot mosaic hyperspectral endoscopic images”, Biomedical Optics Express (2018), [本文](https://pmc.ncbi.nlm.nih.gov/articles/PMC5854078/)。
関連する実装・検証: M. Hughes, “Real-time processing of fiber bundle endomicroscopy images in Python using PyFibreBundle”, Applied Optics 62(34), 9041–9050 (2023), [DOI 10.1364/AO.503700](https://doi.org/10.1364/AO.503700), [著者の実装説明](https://pyfibrebundle.readthedocs.io/en/latest/linear_interp.html)。

原理は、ファイバーの標本を不規則な座標上の測定値として扱い、Delaunay三角形分割から画像を再構成するものです。
校正画像がない今回の条件に合わせ、log画像から格子帯域を取り出し、その時間中央値を使って透過率Gを推定します。
Gの明るい中心を間隔制約付きで選択。各標本値は、小さなコア内の測定値Yに対する min_c sum(G*c-Y)^2 の解で求めます。
標本位置の三角形分割とClough–TocherのC1連続三次補間で再構成します。凸包外は元画像を残します。

変更点: 実測flat-fieldではなく動画からGを推定すること、原論文の線形重心補間をC1三次補間に置き換えること、フィールドごとに校正すること。
PyFibreBundle自体のGaussian前処理やコア検出ルーチンは使っていません。
限界: Gとコア位置の推定に依存し、位相移動・カメラのずれに弱い。見た目がなめらかになる一方、細部・明るさの変化や補間の過不足があり得る。生成モデルではないが、補間した画素は直接観測した画素と同一ではない。

## B: 比較した反復復元（本番CLIには未採用）

Liu et al., “Honeycomb pattern removal for fiber bundle endomicroscopy based on a two-step iterative shrinkage thresholding algorithm”, AIP Advances 10, 045004 (2020), [DOI 10.1063/1.5143929](https://doi.org/10.1063/1.5143929)。
原論文は一様像から透過率を求め、光学PSFを含む観測モデルにTV正則化を加え、TwISTで解きます。

比較試作では一様像とPSFがないため、y = G*x + noise と近似し、min_{0<=x<=1} 0.5||G*x-y||^2 + lambda*TV(x) を解きました。
解法はTwISTの再現ではなく、[Chambolle–Pockの主双対法](https://doi.org/10.1007/s10851-010-0251-1)です。
既定比較はlambda=0.012、160反復、tau=sigma=0.3。Gは動画から推定。
2動画で格子残りが多く、計算時間も大きかったため採用しませんでした。この結果は、実測校正とPSFを使う原論文の性能の否定を意味しません。

## 調査した近年の学習型手法

- Chen et al., Endoir, Optics and Lasers in Engineering 184, 108588 (2025), [DOI 10.1016/j.optlaseng.2024.108588](https://doi.org/10.1016/j.optlaseng.2024.108588): GANによる復元。
- Ma et al., SGARNet, Light: Advanced Manufacturing 7, 50 (2026), [本文](https://www.light-am.com/en/article/doi/10.37188/lam.2026.050): 物理的な格子周波数の事前情報を特徴空間の学習マスクに組み込むネットワーク。
- [Seeing Through Fibersの著者実装](https://github.com/princeton-computational-imaging/Neural-Field-Fiber-Bundle-Imaging): 校正や正解画像を使わない神経場による再構成。ただしネットワークを最適化する方式であり、今回の非学習型という選択に合わせて未採用。

新しい論文であることだけで採用せず、提示動画・CPU・校正なし・細部生成なしという条件に合わせて方式を選びました。

