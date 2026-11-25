"""Timestamp-preserving H.264 I/O; audio packets are remuxed unchanged."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from fractions import Fraction
from itertools import islice
import hashlib
import math
from pathlib import Path
import re
from threading import local
import uuid

import av
from av.video.reformatter import VideoReformatter
import numpy as np


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024*1024), b""):
            value.update(chunk)
    return value.hexdigest()


def time_digest(times):
    value = hashlib.sha256()
    for timestamp in times:
        value.update(f"{timestamp.numerator}/{timestamp.denominator}\n".encode())
    return value.hexdigest()


def _field_order_graph(frame):
    # PyAV 16 exposes interlaced_frame, but not top_field_first.
    graph = av.filter.Graph()
    root = graph.add_buffer(template=frame)
    info = graph.add("showinfo", "checksum=0")
    sink = graph.add("buffersink")
    root.link_to(info); info.link_to(sink); graph.configure()
    return graph


def field_order(frame, graph=None):
    if not frame.interlaced_frame:
        return "progressive"
    if graph is None:
        graph = _field_order_graph(frame)
    previous = av.logging.get_level()
    try:
        av.logging.set_level(av.logging.INFO)
        with av.logging.Capture() as logs:
            graph.push(frame)
            graph.pull()
    finally:
        # Decoder worker threads may already be inside PyAV's log callback.
        # Restoring None changes its shared threshold to None before callbacks
        # finish, causing an int/None comparison race in PyAV 16. Keep a numeric
        # threshold when logging was originally disabled.
        av.logging.set_level(previous if previous is not None else av.logging.ERROR)
    match = re.search(r"\bi:([TBP])\b", "".join(row[2] for row in logs))
    if match is None or match[1] == "P":
        raise ValueError("インターレースのフィールド順を確認できません。")
    return "tff" if match[1] == "T" else "bff"


def _field_order_reader():
    graph = None
    layout = None

    def read(frame):
        nonlocal graph, layout
        if frame.interlaced_frame:
            current = frame.width, frame.height, frame.format.name, frame.time_base
            if current != layout:
                graph = _field_order_graph(frame)
                layout = current
        return field_order(frame, graph)

    return read


def rgb_frame(frame, reformatter=None):
    color = frame.colorspace if frame.colorspace in (1, 4, 5, 7) else None
    if reformatter is None:
        return frame.reformat(format="rgb24", src_colorspace=color).to_ndarray()
    return reformatter.reformat(frame, format="rgb24", src_colorspace=color).to_ndarray()


@dataclass
class Scan:
    path: Path
    metadata: dict
    times: list
    samples: list
    sample_times: list
    tick: Fraction
    rate: Fraction
    video_index: int
    end_time: Fraction


def scan_video(path, sample_count=24, progress=None):
    path = Path(path).resolve(strict=True)
    if path.suffix.lower() not in (".mp4", ".mov"):
        raise ValueError("対応コンテナは MP4 / MOV です。")
    times, samples, sample_times = [], [], []
    with av.open(str(path), options={"err_detect": "explode"}) as container:
        if len(container.streams.video) != 1:
            raise ValueError("映像ストリームが1本のファイルを指定してください。")
        video = container.streams.video[0]
        ctx = video.codec_context
        if ctx.name != "h264" or ctx.format.name not in ("yuv420p", "yuv422p", "yuv444p", "yuvj420p", "yuvj422p", "yuvj444p"):
            raise ValueError("対応映像は8-bit H.264です。未対応形式を自動変換しません。")
        if any(s.type not in ("video", "audio") for s in container.streams):
            raise ValueError("字幕・データ・タイムコード等の追加ストリームがあるため停止しました。")
        ctx.options = {"err_detect": "explode"}
        ctx.thread_type = "AUTO"
        width, height = video.width, video.height
        pixel_format = ctx.format.name
        start = Fraction(video.start_time or 0)*video.time_base
        if video.duration is None:
            raise ValueError("動画の長さを取得できません。")
        duration = Fraction(video.duration)*video.time_base
        targets = np.linspace(float(start+duration/50), float(start+duration*49/50), sample_count)
        order = None
        read_order = _field_order_reader()
        target_index = 0
        denominator = video.time_base.denominator
        ticks = 0
        last_progress = -1
        for packet in container.demux(video):
            if packet.is_corrupt:
                raise ValueError("破損した映像パケットを検出しました。")
            for frame in packet.decode():
                if frame.is_corrupt or frame.pts is None:
                    raise ValueError("破損したフレーム、または時刻のないフレームを検出しました。")
                if (frame.width, frame.height) != (width, height):
                    raise ValueError("途中で解像度が変わる映像には対応していません。")
                if frame.format.name != pixel_format:
                    raise ValueError("途中で画素形式が変わる映像には対応していません。")
                timestamp = Fraction(frame.pts)*frame.time_base
                if times and timestamp <= times[-1]:
                    raise ValueError("フレーム時刻が単調増加していません。")
                if times:
                    scaled = (timestamp-times[-1])*denominator
                    if scaled.denominator != 1:
                        raise ValueError("途中で映像の時間単位が変化しました。")
                    ticks = math.gcd(ticks, scaled.numerator)
                times.append(timestamp)
                current_order = read_order(frame)
                if order is None:
                    order = current_order
                if current_order != order:
                    raise ValueError("途中で走査方式・フィールド順が変わる映像には対応していません。")
                if target_index < len(targets) and float(timestamp) >= targets[target_index]:
                    samples.append(rgb_frame(frame)); sample_times.append(timestamp)
                    while target_index < len(targets) and float(timestamp) >= targets[target_index]:
                        target_index += 1
                fraction = int(100*float((timestamp-start)/duration))
                if progress and fraction//10 != last_progress:
                    last_progress = fraction//10
                    progress(f"scan {min(100, max(0, fraction))}% ({len(times)} frames)")
        if len(times) < 4 or len(samples) < 4:
            raise ValueError("少なくとも4フレームの校正標本が必要です。")
        deltas = sorted(b-a for a, b in zip(times, times[1:]))
        interval = deltas[len(deltas)//2]
        if times[-1]+interval < start+duration-max(video.time_base, Fraction(1, 1000)):
            raise ValueError("記録された動画終了時刻までデコードできませんでした。")
        rate = 1/interval
        end_time = start+duration
        last_duration = end_time-times[-1]
        if last_duration <= 0:
            raise ValueError("動画の終了時刻が最終フレーム以前になっています。")
        final_ticks = last_duration*denominator
        if final_ticks.denominator != 1:
            raise ValueError("最終フレームの長さを入力時間単位で表現できません。")
        tick = Fraction(math.gcd(ticks, final_ticks.numerator), denominator)
        metadata = dict(path=str(path), width=width, height=height,
                        codec=ctx.name, pixel_format=ctx.format.name,
                        reported_average_rate=str(video.average_rate),
                        decoded_frame_rate=str(rate), decoded_frame_count=len(times),
                        variable_frame_rate=len(set(deltas)) > 1,
                        source_time_base=str(video.time_base), encoder_time_base=str(tick),
                        start_time_seconds=float(times[0]),
                        duration_seconds=float(end_time-times[0]),
                        field_order=order, sample_aspect_ratio=str(video.sample_aspect_ratio or Fraction(1)),
                        color_space=ctx.colorspace, color_range=ctx.color_range,
                        color_primaries=ctx.color_primaries, color_transfer=ctx.color_trc,
                        audio=[dict(index=s.index, codec=s.codec_context.name,
                                    sample_rate=s.codec_context.sample_rate,
                                    layout=s.codec_context.layout.name) for s in container.streams.audio],
                        sample_times_seconds=[float(t-times[0]) for t in sample_times],
                        source_sha256=digest(path))
        return Scan(path, metadata, times, samples, sample_times, tick, rate, video.index, end_time)


def selected_times(scan, start=0.0, duration=None):
    if not math.isfinite(start) or start < 0:
        raise ValueError("start must be finite and nonnegative")
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        raise ValueError("duration must be finite and positive")
    lower = scan.times[0]+Fraction(str(start))
    upper = None if duration is None else lower+Fraction(str(duration))
    result = [t for t in scan.times if t >= lower and (upper is None or t < upper)]
    if not result:
        raise ValueError("指定区間にフレームがありません。")
    return result


def _setfield_graph(frame, order):
    graph = av.filter.Graph()
    source = graph.add_buffer(template=frame)
    setting = graph.add("setfield", "mode="+(order if order != "progressive" else "prog"))
    sink = graph.add("buffersink")
    source.link_to(setting); setting.link_to(sink); graph.configure()
    return graph


def _audio_signature():
    return dict(count=0, payload=hashlib.sha256(), times=hashlib.sha256())


def _record_audio(signature, packet):
    signature["count"] += 1
    signature["payload"].update(bytes(packet))
    for value in (packet.pts, packet.dts, packet.duration):
        t = None if value is None else Fraction(value)*packet.time_base
        signature["times"].update((str(t)+"|").encode())


def _finish_audio(signature):
    return dict(packets=signature["count"], payload_sha256=signature["payload"].hexdigest(),
                timing_sha256=signature["times"].hexdigest())


def process_video(scan, model, destination, *, start=0.0, duration=None, crf=18,
                  preset="medium", overwrite=False, progress=None, frame_workers=1):
    destination = Path(destination).resolve()
    if destination == scan.path:
        raise ValueError("入力ファイルと出力ファイルは別にしてください。")
    if destination.suffix.lower() != scan.path.suffix.lower():
        raise ValueError("出力拡張子を入力と同じ MP4 / MOV にしてください。")
    if destination.exists() and not overwrite:
        raise FileExistsError(f"既存出力は上書きしません: {destination}")
    if not 0 <= crf <= 40:
        raise ValueError("CRF must be between 0 and 40")
    if not 1 <= frame_workers <= 8:
        raise ValueError("frame workers must be between 1 and 8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.stem}.{uuid.uuid4().hex}.partial{destination.suffix}")
    wanted = selected_times(scan, start, duration)
    origin = wanted[0]
    durations = {t: b-t for t, b in zip(scan.times, scan.times[1:]+[scan.end_time])}
    end = wanted[-1]+durations[wanted[-1]]
    expected_times = [t-origin for t in wanted]
    expected_audio = {}
    index = 0
    order = scan.metadata["field_order"]
    try:
        with av.open(str(scan.path), options={"err_detect": "explode"}) as source, \
                av.open(str(temporary), "w", options={"movflags": "+faststart", "avoid_negative_ts": "disabled"}) as output:
            vi = source.streams.video[0]
            # MP4 edit lists use the movie timescale, which defaults to milliseconds.
            movie_timescale = math.lcm(*(stream.time_base.denominator for stream in source.streams))
            if movie_timescale <= 2**31 - 1:
                output.options["movie_timescale"] = str(movie_timescale)
            vi.codec_context.options = {"err_detect": "explode"}
            vi.codec_context.thread_type = "AUTO"
            output.metadata.update(source.metadata)
            vo = output.add_stream("libx264", rate=scan.rate)
            vo.codec_context.thread_type = "FRAME"
            vo.width = vi.width; vo.height = vi.height
            vo.pix_fmt = vi.codec_context.format.name.replace("yuvj", "yuv")
            vo.time_base = vi.time_base
            vo.codec_context.time_base = scan.tick
            vo.codec_context.framerate = scan.rate
            vo.sample_aspect_ratio = vi.sample_aspect_ratio or Fraction(1)
            vo.codec_context.sample_aspect_ratio = vi.sample_aspect_ratio or Fraction(1)
            for attr in ("colorspace", "color_range", "color_primaries", "color_trc"):
                setattr(vo.codec_context, attr, getattr(vi.codec_context, attr))
            vo.metadata.update({k: v for k, v in vi.metadata.items() if k != "encoder"})
            vo.options = {"crf": str(crf), "preset": preset}
            if order != "progressive":
                vo.options["x264-params"] = f"interlaced=1:{order}=1"
                vo.options["weightp"] = "0"
                vo.codec_context.flags |= av.codec.context.Flags.interlaced_dct | av.codec.context.Flags.interlaced_me
            streams = {}
            for ai in source.streams.audio:
                ao = output.add_stream_from_template(ai)
                ao.time_base = ai.time_base
                streams[ai.index] = ao
                expected_audio[ao.index] = _audio_signature()
            graph = None
            conversion = local()
            pixel_format = vo.pix_fmt
            process_inplace = getattr(model, "process_inplace", None)

            def mux_video(item):
                position = Fraction(item.pts)*item.time_base+origin
                if position not in durations:
                    raise ValueError("エンコーダが入力にない表示時刻を生成しました。")
                exact_duration = durations[position]/item.time_base
                if exact_duration.denominator != 1:
                    raise ValueError("フレームの長さを出力時間単位で表現できません。")
                item.duration = exact_duration.numerator
                output.mux(item)

            def prepare_frame(frame):
                colors = frame.colorspace, frame.color_range
                if colors != getattr(conversion, "colors", None):
                    # Cached swscale contexts retain the previous color transform.
                    conversion.to_rgb = VideoReformatter()
                    conversion.from_rgb = VideoReformatter()
                    conversion.colors = colors
                rgb = rgb_frame(frame, conversion.to_rgb)
                if process_inplace is None:
                    encoded_frame = av.VideoFrame.from_ndarray(model.process(rgb), format="rgb24")
                else:
                    process_inplace(rgb)
                    encoded_frame = av.VideoFrame.from_numpy_buffer(rgb, format="rgb24")
                color = frame.colorspace if frame.colorspace in (1, 4, 5, 7) else None
                encoded_frame = conversion.from_rgb.reformat(encoded_frame, format=pixel_format,
                    src_colorspace=color, dst_colorspace=color,
                    src_color_range=2, dst_color_range=frame.color_range or 1)
                encoded_frame.colorspace = frame.colorspace
                encoded_frame.color_range = frame.color_range
                return encoded_frame

            def write_frame(timestamp, encoded_frame):
                nonlocal graph, index
                ticks = (timestamp-origin)/scan.tick
                if ticks.denominator != 1:
                    raise ValueError("入力時刻を出力時間軸で正確に表現できません。")
                encoded_frame.pts = ticks.numerator
                encoded_frame.time_base = scan.tick
                encoded_frame.duration = int(durations[timestamp]/scan.tick)
                if graph is None:
                    graph = _setfield_graph(encoded_frame, order)
                graph.push(encoded_frame)
                for item in vo.encode(graph.pull()):
                    mux_video(item)
                index += 1
                if progress and (index == 1 or index % 100 == 0 or index == len(wanted)):
                    progress(f"process {index}/{len(wanted)} frames ({100*index/len(wanted):.1f}%)")

            def write_audio(packet):
                presentation = Fraction(packet.pts if packet.pts is not None else packet.dts)*packet.time_base
                packet_end = presentation+Fraction(packet.duration)*packet.time_base
                # For full-length output retain the source's priming/trailing
                # packets. Preview boundaries are whole compressed audio packets.
                if start > 0 and packet_end <= origin:
                    return
                if duration is not None and presentation >= end:
                    return
                offset = round(origin/packet.time_base)
                if packet.pts is not None:
                    packet.pts -= offset
                packet.dts -= offset
                ao = streams[packet.stream.index]
                _record_audio(expected_audio[ao.index], packet)
                packet.stream = ao
                output.mux(packet)

            def read_items():
                submitted = 0
                for packet in source.demux():
                    if packet.is_corrupt:
                        raise ValueError("破損したパケットを検出しました。")
                    if packet.stream.index == vi.index:
                        for frame in packet.decode():
                            if frame.is_corrupt or frame.pts is None:
                                raise ValueError("フレームの破損または時刻欠落を検出しました。")
                            timestamp = Fraction(frame.pts)*frame.time_base
                            if timestamp < origin or timestamp > wanted[-1]:
                                continue
                            if submitted >= len(wanted) or timestamp != wanted[submitted]:
                                raise ValueError("校正後に入力のフレーム時刻が変化しました。")
                            submitted += 1
                            yield timestamp, frame
                    elif packet.stream.index in streams and packet.dts is not None:
                        yield None, packet

            def prepare_items():
                if frame_workers == 1:
                    for timestamp, item in read_items():
                        yield timestamp, item if timestamp is None else prepare_frame(item)
                    return

                # Read one bounded batch ahead while preserving the mux order.
                pending = deque()
                items = read_items()
                batch_size = 8 * frame_workers
                with (
                    ThreadPoolExecutor(max_workers=1) as reader,
                    ThreadPoolExecutor(max_workers=frame_workers) as executor,
                ):
                    reading = reader.submit(list, islice(items, batch_size))
                    while batch := reading.result():
                        reading = reader.submit(list, islice(items, batch_size))
                        for timestamp, item in batch:
                            prepared = item if timestamp is None else executor.submit(prepare_frame, item)
                            pending.append((timestamp, prepared))
                            if len(pending) >= 4 * frame_workers:
                                ts, prepared = pending.popleft()
                                yield ts, prepared if ts is None else prepared.result()
                    for ts, prepared in pending:
                        yield ts, prepared if ts is None else prepared.result()

            for timestamp, item in prepare_items():
                if timestamp is None:
                    write_audio(item)
                else:
                    write_frame(timestamp, item)
            for item in vo.encode():
                mux_video(item)
        if index != len(wanted):
            raise ValueError("予定された全フレームを書き出せませんでした。")
        with ThreadPoolExecutor(max_workers=1) as executor:
            checksum = executor.submit(digest, temporary)
            verified = verify_output(temporary, scan, expected_times,
                                     {k: _finish_audio(v) for k, v in expected_audio.items()}, end-origin)
            output_hash = checksum.result()
        if destination.exists() and not overwrite:
            raise FileExistsError(f"処理中に出力先が作成されました: {destination}")
        temporary.replace(destination)
        return dict(path=str(destination), sha256=output_hash, crf=crf, preset=preset,
                    frames=index, start_seconds=float(origin-scan.times[0]),
                    duration_seconds=float(end-origin), verification=verified)
    finally:
        if temporary.exists():
            temporary.unlink()


def verify_output(path, scan, expected_times, expected_audio, expected_duration):
    times = []
    audio = {}
    order = scan.metadata["field_order"]
    read_order = _field_order_reader()
    with av.open(str(path), options={"err_detect": "explode"}) as container:
        video = container.streams.video[0]
        video.codec_context.thread_type = "AUTO"
        if video.duration is None or Fraction(video.duration)*video.time_base != expected_duration:
            raise ValueError("出力映像の長さが入力区間と一致しません。")
        if (video.width, video.height) != (scan.metadata["width"], scan.metadata["height"]):
            raise ValueError("出力解像度が入力と一致しません。")
        for stream in container.streams.audio:
            audio[stream.index] = _audio_signature()
        for packet in container.demux():
            if packet.stream.type == "video":
                for frame in packet.decode():
                    if frame.is_corrupt or frame.pts is None:
                        raise ValueError("出力映像の検証に失敗しました。")
                    if read_order(frame) != order:
                        raise ValueError("出力のフィールド順が入力と一致しません。")
                    if bool(frame.interlaced_frame) != (order != "progressive"):
                        raise ValueError("出力のインターレース状態が入力と一致しません。")
                    times.append(Fraction(frame.pts)*frame.time_base)
            elif packet.stream.type == "audio" and packet.dts is not None:
                _record_audio(audio[packet.stream.index], packet)
    if times != expected_times:
        raise ValueError("出力のフレーム数またはフレーム時刻が入力と一致しません。")
    audio = {k: _finish_audio(v) for k, v in audio.items()}
    if audio != expected_audio:
        raise ValueError("音声のパケット内容または時刻の検証に失敗しました。")
    return dict(decoded_frame_count=len(times), presentation_timestamps_exact=True,
                timestamp_sha256=time_digest(times), field_order=order,
                audio_packet_content_and_timing_exact=True, audio_streams=audio)
