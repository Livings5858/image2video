#!/usr/bin/env python3
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import os
import threading
import subprocess
import sys
import tempfile
import shutil
import math
from datetime import datetime
import json
from PIL import Image, ImageTk, ImageOps  # 用于图像预览

# 第三方工具路径配置
base_path = os.path.abspath(".")
thirdparty_dir = os.path.join(base_path, 'thirdparty')

def get_exif_data(image_path):
    """获取EXIF信息"""
    try:
        result = subprocess.run(
            [os.path.join(thirdparty_dir, 'exiftool'), '-make', '-ISO', '-ShutterSpeed', "-Fnumber", "-fast", image_path],
            capture_output=True,
            text=True
        )
        lines = result.stdout.strip().split('\n')
        return {
            'make': lines[0].replace(' ', '').split(':')[1] if len(lines) > 0 else 'N/A',
            'iso': lines[1].replace(' ', '').split(':')[1] if len(lines) > 1 else 'N/A',
            'shutter': lines[2].replace(' ', '').split(':')[1] if len(lines) > 2 else 'N/A',
            'fnumber': lines[3].replace(' ', '').split(':')[1] if len(lines) > 3 else 'N/A'
        }
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {'iso': 'N/A', 'shutter': 'N/A'}

def process_image(input_path, output_path, text=""):
    """处理单张图片，添加16:9背景和信息栏"""
    # 获取图片尺寸
    try:
        result = subprocess.run(
            [os.path.join(thirdparty_dir, 'ffprobe'), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", input_path],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            print(f"Error getting image dimensions for {input_path}")
            print(result.stderr)
            return False
    except subprocess.TimeoutExpired:
        print(f"Timeout getting dimensions for {input_path}")
        return False

    dimensions = result.stdout.strip().split(',')
    if len(dimensions) < 2:
        print(f"Invalid dimension data for {input_path}: {result.stdout}")
        return False
    
    try:
        width = int(dimensions[0])
        height = int(dimensions[1])
    except ValueError:
        print(f"Invalid dimension values for {input_path}: {dimensions}")
        return False

    # 计算16:9背景尺寸
    bg_height = int(width * 9 / 16)
    bg_width = width

    # 计算信息区域高度（16:9高度的12%）
    info_height = int(bg_height * 0.2)

    # 总高度
    total_height = bg_height + info_height

    # 自动生成信息文本
    if not text:
        exif_data = get_exif_data(input_path)
        if exif_data['iso'] != 'N/A' and exif_data['shutter'] != 'N/A' and exif_data['fnumber'] != 'N/A':
            text = f"{exif_data['make']} f/{exif_data['fnumber']} {exif_data['shutter']}s ISO{exif_data['iso']}"
        else:
            text = f"Processed {datetime.now().strftime('%Y-%m-%d')}"

    # 计算文字大小（基于总高度的2.5%）
    font_size = max(40, int(total_height * 0.025))

    # 阴影参数
    shadow_size = 10
    shadow_opacity = 0.05

    # 构建FFmpeg命令
    ffmpeg_cmd = [
        os.path.join(thirdparty_dir, 'ffmpeg'), "-i", input_path, "-filter_complex",
        f"[0:v]scale=w={bg_width}:h={bg_height}:force_original_aspect_ratio=decrease[fg];"
        f"[0:v]scale=w={bg_width}:h={total_height}:force_original_aspect_ratio=increase,"
        f"crop=w={bg_width}:h={total_height},boxblur=30:10[bg];"
        f"[fg]pad=w=iw+{shadow_size}*2:h=ih+{shadow_size}*2:x={shadow_size}:y={shadow_size}:"
        f"color=black@{shadow_opacity},split[fg_padded][fg_shadow];"
        f"[fg_shadow]boxblur=10:10[shadow];"
        f"[bg][shadow]overlay=x=(W-w)/2:y=({total_height}-h)/2-30[combined];"
        f"[combined][fg_padded]overlay=x=(W-w)/2:y=({total_height}-h)/2-30[base];"
        f"[base]drawtext=text='{text}':fontcolor=white:fontsize={font_size}:"
        f"x=(w-tw)/2:y={bg_height}+{int(info_height/2)}-{int(font_size/2)}+50",
        "-y", output_path
    ]

    # 执行命令并捕获输出
    try:
        completed = subprocess.run(
            ffmpeg_cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT,
            timeout=30  # 30秒超时
        )
    except subprocess.TimeoutExpired:
        print(f"Processing image {input_path} timed out")
        return False

    # 检查执行结果
    if completed.returncode != 0:
        print(f"Error processing image {input_path}:\n{completed.stdout.decode()}")
        return False
    
    return True

def get_audio_duration(audio_path):
    """获取音频文件时长（秒）"""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'json', audio_path],
            capture_output=True, text=True, timeout=10
        )
        data = json.loads(result.stdout)
        return float(data['format']['duration'])
    except (subprocess.CalledProcessError, KeyError, json.JSONDecodeError):
        return 0
    except subprocess.TimeoutExpired:
        print(f"Timeout getting audio duration for {audio_path}")
        return 0

def process_audio_for_video(audio_path, video_duration, temp_dir):
    """处理音频文件以适应视频长度（循环或截断）"""
    # 创建临时音频文件
    processed_audio = os.path.join(temp_dir, "processed_audio.mp3")
    
    # 获取音频长度
    audio_duration = get_audio_duration(audio_path)
    
    if audio_duration <= 0:
        print(f"Warning: Could not get duration for audio file {audio_path}")
        return None
    
    # 计算需要循环的次数
    loop_count = math.ceil(video_duration / audio_duration)
    
    # 构建FFmpeg命令 - 使用更简单的格式
    ffmpeg_cmd = [
        os.path.join(thirdparty_dir, 'ffmpeg'),
        "-y",
        "-stream_loop", str(loop_count),
        "-i", audio_path,
        "-t", str(video_duration),  # 截断到视频长度
        "-vn",  # 不处理视频
        "-acodec", "libmp3lame",  # 更兼容的MP3编码器
        "-b:a", "192k",
        "-f", "mp3",  # 强制输出为MP3
        processed_audio
    ]
    
    # 执行命令
    try:
        completed = subprocess.run(
            ffmpeg_cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT,
            timeout=120  # 2分钟超时
        )
    except subprocess.TimeoutExpired:
        print(f"Audio processing timed out for {audio_path}")
        return None
    
    if completed.returncode != 0:
        print(f"Error processing audio:\n{completed.stdout.decode()}")
        return None
    
    return processed_audio

def is_encoder_available(encoder_name):
    """检查编码器是否可用"""
    try:
        result = subprocess.run(
            [os.path.join(thirdparty_dir, 'ffmpeg'), "-codecs"],
            capture_output=True,
            text=True,
            timeout=10
        )
        # 搜索编码器是否在输出列表中
        return encoder_name in result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    except subprocess.TimeoutExpired:
        print("Timed out checking codecs")
        return False

def create_video(image_paths, output_video, duration, music=None, progress_callback=None):
    """从处理后的图片创建视频"""
    # 创建临时目录存放所有处理过的图片
    temp_dir = tempfile.mkdtemp(prefix='video_frames_')
    processed_images = []
    total_images = len(image_paths)
    
    print(f"Processing {total_images} images...")
    for i, img_path in enumerate(image_paths):
        # 为每张图片生成输出路径
        output_path = os.path.join(temp_dir, f"frame_{i:04d}.png")
        if process_image(img_path, output_path):
            processed_images.append(output_path)
            print(f"Processed {i+1}/{total_images}: {img_path}")
            # 更新进度条（图片处理阶段占比70%）
            if progress_callback:
                progress_callback(i + 1, total_images, "processing")
        else:
            print(f"Skipping {img_path} due to processing error")
    
    if not processed_images:
        print("No valid images processed. Exiting.")
        shutil.rmtree(temp_dir)
        return False
    
    # 计算视频总时长
    total_duration = len(processed_images) * duration
    
    # 处理音频（如果需要）
    final_audio = None
    if music:
        print("Processing background music...")
        final_audio = process_audio_for_video(music, total_duration, temp_dir)
        if not final_audio:
            print("Using video without audio due to audio processing error")
    
    # 检查可用的编码器 - 优先使用兼容性编码器
    if is_encoder_available("libx264"):
        print("Using libx264 encoder")
        video_codec = "libx264"
        pix_fmt = "yuv420p"
        bitrate_options = ["-b:v", "8M"]
    else:
        print("Using H.264 encoder")
        video_codec = "h264"
        pix_fmt = "yuv420p"
        bitrate_options = ["-b:v", "8M"]
    
    # 构建FFmpeg视频创建命令
    ffmpeg_cmd = [
        os.path.join(thirdparty_dir, 'ffmpeg'), "-y",
        "-framerate", str(1/float(duration)), 
        "-i", os.path.join(temp_dir, "frame_%04d.png"),
    ]
    
    # 添加音频输入（如果有）
    if final_audio:
        ffmpeg_cmd.extend(["-i", final_audio])
    
    # 设置输出选项
    ffmpeg_cmd.extend([
        "-c:v", video_codec,
        *bitrate_options,
        "-r", "25",   # 输出帧率25fps
        "-pix_fmt", pix_fmt,
        "-movflags", "+faststart",
    ])
    
    # 设置音频选项（如果有音频）
    if final_audio:
        ffmpeg_cmd.extend([
            "-c:a", "aac",  # 使用AAC音频编码器
            "-b:a", "192k",
            "-map", "0:v:0", "-map", "1:a:0"  # 明确映射视频和音频流
        ])
    else:
        ffmpeg_cmd.append("-an")  # 没有音频
    
    ffmpeg_cmd.append(output_video)
    
    # 执行视频创建命令
    print("Creating video...")
    print("Executing command:", " ".join(ffmpeg_cmd))
    
    # 通知UI开始视频合成阶段
    if progress_callback:
        progress_callback(0, 0, "compiling")
    
    try:
        # 启动视频合成进程
        with subprocess.Popen(
            ffmpeg_cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT,
            text=True
        ) as proc:
            # 读取输出以捕获错误（但不需要显示进度）
            for line in proc.stdout:
                if progress_callback:
                    # 简单模拟视频合成进度（从70%到100%）
                    progress = 70 + 30 * (proc.poll() is None) / 2
                    progress_callback(progress, 100, "compiling")
                # 可以在这里解析FFmpeg进度信息（如果更精确的进度跟踪是必需的）
                pass
                
            # 等待进程完成
            proc.wait()
            returncode = proc.returncode
    except Exception as e:
        print(f"Video creation error: {str(e)}")
        returncode = -1
    
    # 清理临时文件
    shutil.rmtree(temp_dir)
    
    if returncode != 0:
        print(f"Error creating video (return code {returncode})")
        return False
    
    print(f"Successfully created video: {output_video}")
    print(f"Video duration: {total_duration} seconds")
    print(f"Number of images: {len(processed_images)}")
    progress_callback(progress, 100, "compiling")
    return True

class VideoCreatorApp:
    def __init__(self, root):
        self.root = root
        root.title("PicVideo Pro")
        root.geometry("1000x800")  # 调整窗口尺寸
        root.resizable(True, True)
        
        # 设置应用图标
        try:
            root.iconbitmap("PicVideo_Pro.ico")  # Windows系统
        except:
            pass  # Linux/macOS可能不支持
        
        # 样式配置（使用系统默认外观）
        self.style = ttk.Style()
        self.style.configure("TFrame", padding=10)
        self.style.configure("TLabel", padding=5)
        self.style.configure("TButton", padding=5)
        self.style.configure("TLabelframe.Label", background='SystemButtonFace')  # 恢复默认背景
        
        # 创建主框架：上下两部分
        main_frame = ttk.Frame(root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # 上部框架：左右两部分（预览和设置）
        top_frame = ttk.Frame(main_frame)
        top_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # 左侧预览面板
        preview_frame = ttk.LabelFrame(top_frame, text="预览区域")
        preview_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # 创建16:9比例的预览画布
        self.preview_canvas = tk.Canvas(preview_frame, bg='SystemButtonFace')
        self.preview_canvas.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.preview_image = None
        
        # 右侧设置面板
        settings_frame = ttk.LabelFrame(top_frame, text="设置面板")
        settings_frame.pack(side=tk.RIGHT, fill=tk.BOTH, padx=(10, 0))
        
        # 图片选择区
        img_frame = ttk.LabelFrame(settings_frame, text="图片选择")
        img_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # 创建图片列表框
        list_frame = ttk.Frame(img_frame)
        list_frame.pack(fill=tk.X, padx=5, pady=(0, 5))
        
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.img_listbox = tk.Listbox(
            list_frame, 
            height=8, 
            selectmode=tk.EXTENDED,
            yscrollcommand=scrollbar.set
        )
        self.img_listbox.pack(fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.img_listbox.yview)
        
        # 绑定选择事件
        self.img_listbox.bind('<<ListboxSelect>>', self.on_image_select)
        
        # 按钮区域
        btn_frame = ttk.Frame(img_frame)
        btn_frame.pack(fill=tk.X)
        
        ttk.Button(btn_frame, text="添加文件", command=self.add_files).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="添加目录", command=self.add_directory).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="移除", command=self.remove_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="清空", command=self.clear_list).pack(side=tk.LEFT, padx=2)
        
        # 输出设置
        output_frame = ttk.LabelFrame(settings_frame, text="输出设置")
        output_frame.pack(fill=tk.X, padx=5, pady=5)
        
        output_row1 = ttk.Frame(output_frame)
        output_row1.pack(fill=tk.X, pady=3)
        ttk.Label(output_row1, text="输出视频文件:").pack(side=tk.LEFT)
        self.output_var = tk.StringVar(value="output.mp4")
        output_entry = ttk.Entry(output_row1, textvariable=self.output_var)
        output_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ttk.Button(output_row1, text="浏览...", command=self.browse_output).pack(side=tk.RIGHT, padx=5)
        
        output_row2 = ttk.Frame(output_frame)
        output_row2.pack(fill=tk.X, pady=3)
        ttk.Label(output_row2, text="每张图片持续时间(秒):").pack(side=tk.LEFT)
        self.duration_var = tk.DoubleVar(value=7)
        duration_entry = ttk.Entry(output_row2, textvariable=self.duration_var, width=8)
        duration_entry.pack(side=tk.LEFT, padx=5)
        
        # 音乐设置
        music_frame = ttk.LabelFrame(settings_frame, text="音频设置")
        music_frame.pack(fill=tk.X, padx=5, pady=5)
        
        music_row = ttk.Frame(music_frame)
        music_row.pack(fill=tk.X, pady=3)
        ttk.Label(music_row, text="背景音乐:").pack(side=tk.LEFT)
        self.music_var = tk.StringVar()
        music_entry = ttk.Entry(music_row, textvariable=self.music_var)
        music_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ttk.Button(music_row, text="浏览...", command=self.browse_music).pack(side=tk.RIGHT, padx=5)
        
        # 文本设置
        text_frame = ttk.LabelFrame(settings_frame, text="文本设置")
        text_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(text_frame, text="叠加文本:").pack(anchor=tk.W, padx=5, pady=(2, 0))
        self.text_var = tk.StringVar()
        text_entry = ttk.Entry(text_frame, textvariable=self.text_var)
        text_entry.pack(fill=tk.X, padx=5, pady=(0, 3))
        ttk.Label(text_frame, text="留空则使用EXIF信息自动生成", 
                 font=("Arial", 8), foreground="#666666").pack(anchor=tk.W, padx=5)
        
        # 控制按钮
        control_frame = ttk.Frame(settings_frame)
        control_frame.pack(fill=tk.X, padx=5, pady=(10, 5))
        
        self.create_btn = ttk.Button(control_frame, text="创建视频", command=self.start_creation)
        self.create_btn.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(control_frame, text="退出", command=root.quit).pack(side=tk.RIGHT, padx=5)
        
        # 下部框架：进度条和日志
        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.pack(fill=tk.BOTH)
        
        # 进度条
        progress_frame = ttk.Frame(bottom_frame)
        progress_frame.pack(fill=tk.X, pady=(0, 5))
        
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(
            progress_frame, 
            variable=self.progress_var, 
            maximum=100, 
            mode='determinate'
        )
        self.progress_bar.pack(fill=tk.X, padx=5)
        
        # 进度标签
        self.progress_label = ttk.Label(progress_frame, text="准备就绪")
        self.progress_label.pack(fill=tk.X, padx=5, pady=2)
        
        # 日志区域
        log_frame = ttk.LabelFrame(bottom_frame, text="处理日志")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 5))
        
        self.log_text = tk.Text(log_frame, height=6)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        scrollbar_log = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar_log.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar_log.set)
        
        # 重定向标准输出
        self.original_stdout = sys.stdout
        sys.stdout = self
        
        # 线程控制
        self.creation_thread = None
        self.preview_thread = None
        self.stop_flag = False
        
        # 预览组件
        self.temp_preview_path = None
        self.current_preview = None
        
        # 初始状态
        self.progress_var.set(0)
        self.update_progress_label(0, 0)
        
    def write(self, message):
        """重定向print到日志文本框"""
        self.log_text.insert(tk.END, message)
        self.log_text.see(tk.END)
        
    def flush(self):
        """标准输出需要的方法"""
        pass
    
    def update_progress_label(self, current, total, phase=None):
        """更新进度标签显示"""
        if phase == "processing":
            self.progress_label.config(text=f"图片处理中: {current}/{total} ({current/total*100:.1f}%)")
        elif phase == "compiling":
            if total == 0:
                self.progress_label.config(text="视频合成中...")
            else:
                self.progress_label.config(text=f"视频合成中: {current:.1f}%")
        else:
            self.progress_label.config(text="准备就绪" if current == 0 else f"完成进度: {current:.1f}%")
    
    def add_files(self):
        """添加图片文件"""
        files = filedialog.askopenfilenames(
            filetypes=[("图片文件", "*.jpg *.jpeg *.png *.bmp *.tiff *.jpe")]
        )
        for file in files:
            if file not in self.img_listbox.get(0, tk.END):
                self.img_listbox.insert(tk.END, file)
    
    def add_directory(self):
        """添加目录中的所有图片"""
        directory = filedialog.askdirectory()
        if directory:
            for root, _, files in os.walk(directory):
                for file in files:
                    if file.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.jpe')):
                        path = os.path.join(root, file)
                        if path not in self.img_listbox.get(0, tk.END):
                            self.img_listbox.insert(tk.END, path)
    
    def remove_selected(self):
        """移除选中的项目"""
        selected = self.img_listbox.curselection()
        for i in selected[::-1]:  # 反向删除避免索引变化
            self.img_listbox.delete(i)
    
    def clear_list(self):
        """清空图片列表"""
        self.img_listbox.delete(0, tk.END)
        self.clear_preview()
    
    def browse_output(self):
        """选择输出文件"""
        file = filedialog.asksaveasfilename(
            defaultextension=".mp4",
            filetypes=[("MP4视频", "*.mp4"), ("所有文件", "*.*")]
        )
        if file:
            self.output_var.set(file)
    
    def browse_music(self):
        """选择背景音乐文件"""
        file = filedialog.askopenfilename(
            filetypes=[("音频文件", "*.mp3 *.wav *.ogg"), ("所有文件", "*.*")]
        )
        if file:
            self.music_var.set(file)
    
    def on_image_select(self, event):
        """当选择图片时自动预览"""
        selected = self.img_listbox.curselection()
        if not selected:
            return
            
        img_path = self.img_listbox.get(selected[0])
        if not os.path.exists(img_path):
            return
            
        # 清除之前的预览
        self.clear_preview()
        
        # 显示加载提示
        self.preview_canvas.delete("all")
        self.canvas_text = self.preview_canvas.create_text(
            self.preview_canvas.winfo_width()/2, 
            self.preview_canvas.winfo_height()/2,
            text="正在加载预览...",
            fill="#666666",
            font=("Arial", 12)
        )
        
        # 在后台线程中加载预览
        self.preview_thread = threading.Thread(
            target=self.load_preview,
            args=(img_path,),
            daemon=True
        )
        self.preview_thread.start()
        
    def load_preview(self, img_path):
        """在后台线程中加载预览图片"""
        try:
            # 清理上一次的临时预览文件
            if self.temp_preview_path and os.path.exists(self.temp_preview_path):
                try:
                    os.remove(self.temp_preview_path)
                except:
                    pass
            
            # 创建临时预览文件
            temp_dir = tempfile.gettempdir()
            self.temp_preview_path = os.path.join(temp_dir, f"preview_{os.path.basename(img_path)}")
            
            # 处理图片（使用空文本生成预览）
            if process_image(img_path, self.temp_preview_path, text=""):
                # 在UI线程中显示处理后的图片
                self.root.after(0, self.display_preview, img_path)
            else:
                # 处理失败则显示原图
                self.root.after(0, self.display_original, img_path)
        except Exception as e:
            print(f"预览加载错误: {str(e)}")
            
    def display_preview(self, img_path):
        """在预览区显示处理后的图片"""
        if not os.path.exists(self.temp_preview_path):
            self.display_original(img_path)
            return
            
        try:
            # 获取画布尺寸
            canvas_width = self.preview_canvas.winfo_width()
            canvas_height = self.preview_canvas.winfo_height()
            
            # 如果画布大小太小，使用默认预览尺寸
            if canvas_width < 100 or canvas_height < 100:
                canvas_width = 500
                canvas_height = 280  # 16:9的比例 (500 * 9/16)
            
            # 加载处理后的图片
            img = Image.open(self.temp_preview_path)
            
            # 调整大小适应预览区域
            aspect_ratio = img.width / img.height
            target_width = canvas_width - 20  # 左右留出10px边距
            target_height = int(target_width / aspect_ratio)
            
            # 如果高度超出，则根据高度调整
            if target_height > canvas_height - 20:  # 上下留出10px边距
                target_height = canvas_height - 20
                target_width = int(target_height * aspect_ratio)
                
            img = img.resize((target_width, target_height), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            
            # 清除画布并显示图片
            self.preview_canvas.delete("all")
            self.preview_image = photo  # 保持引用防止被垃圾回收
            
            # 计算居中位置
            x = canvas_width / 2
            y = canvas_height / 2
            
            self.preview_canvas.create_image(
                x, y, 
                anchor=tk.CENTER, 
                image=photo
            )
            
            # 添加标题
            self.preview_canvas.create_text(
                x, 15,
                text="预览效果",
                fill="#333333",
                font=("Arial", 10)
            )
            
            # 添加边框
            self.preview_canvas.create_rectangle(
                x - target_width/2 - 5, y - target_height/2 - 5,
                x + target_width/2 + 5, y + target_height/2 + 5,
                outline="#cccccc", 
                width=1
            )
            
        except Exception as e:
            print(f"预览显示错误: {str(e)}")
            self.clear_preview()
            
    def display_original(self, img_path):
        """在预览区显示原图"""
        try:
            # 获取画布尺寸
            canvas_width = self.preview_canvas.winfo_width()
            canvas_height = self.preview_canvas.winfo_height()
            
            # 如果画布大小太小，使用默认预览尺寸
            if canvas_width < 100 or canvas_height < 100:
                canvas_width = 500
                canvas_height = 280  # 16:9的比例 (500 * 9/16)
            
            # 加载原始图片
            img = Image.open(img_path)
            
            # 调整大小适应预览区域
            aspect_ratio = img.width / img.height
            target_width = canvas_width - 20  # 左右留出10px边距
            target_height = int(target_width / aspect_ratio)
            
            # 如果高度超出，则根据高度调整
            if target_height > canvas_height - 20:  # 上下留出10px边距
                target_height = canvas_height - 20
                target_width = int(target_height * aspect_ratio)
                
            img = img.resize((target_width, target_height), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            
            # 清除画布并显示图片
            self.preview_canvas.delete("all")
            self.preview_image = photo  # 保持引用防止被垃圾回收
            
            # 计算居中位置
            x = canvas_width / 2
            y = canvas_height / 2
            
            self.preview_canvas.create_image(
                x, y, 
                anchor=tk.CENTER, 
                image=photo
            )
            
            # 添加标题
            self.preview_canvas.create_text(
                x, 15,
                text="原始图片预览",
                fill="#333333",
                font=("Arial", 10)
            )
            
            # 添加边框
            self.preview_canvas.create_rectangle(
                x - target_width/2 - 5, y - target_height/2 - 5,
                x + target_width/2 + 5, y + target_height/2 + 5,
                outline="#cccccc", 
                width=1
            )
            
            # 添加提示文字
            self.preview_canvas.create_text(
                x, canvas_height - 20,
                text="预览生成失败，显示原始图片",
                fill="#ff0000",
                font=("Arial", 10)
            )
        except Exception as e:
            print(f"原图预览错误: {str(e)}")
            self.clear_preview()
            
    def clear_preview(self):
        """清除预览区域"""
        self.preview_canvas.delete("all")
        self.canvas_text = self.preview_canvas.create_text(
            self.preview_canvas.winfo_width()/2, 
            self.preview_canvas.winfo_height()/2,
            text="选择图片预览效果",
            fill="#666666",
            font=("Arial", 14)
        )
        self.preview_image = None
    
    def start_creation(self):
        """开始创建视频"""
        # 获取输入数据
        img_paths = self.img_listbox.get(0, tk.END)
        output_path = self.output_var.get()
        duration = self.duration_var.get()
        music = self.music_var.get() if self.music_var.get() else None
        text = self.text_var.get()
        
        # 验证输入
        if not img_paths:
            messagebox.showerror("错误", "请添加至少一张图片")
            return
            
        if not output_path:
            messagebox.showerror("错误", "请指定输出文件路径")
            return
            
        try:
            duration = float(duration)
            if duration <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "持续时间必须是一个正数")
            return
        
        # 准备参数
        MAX_IMAGES = 100
        if len(img_paths) > MAX_IMAGES:
            img_paths = img_paths[:MAX_IMAGES]
            messagebox.showwarning("警告", f"图片数量超过{MAX_IMAGES}张限制，已使用前{MAX_IMAGES}张")
        
        # 清空日志和进度
        self.log_text.delete(1.0, tk.END)
        self.progress_var.set(0)
        self.progress_label.config(text="开始处理图片...")
        self.stop_flag = False
        
        # 禁用按钮
        self.create_btn.config(state=tk.DISABLED)
        
        # 在后台线程中创建视频
        self.creation_thread = threading.Thread(
            target=self.run_creation,
            args=(img_paths, output_path, duration, music, text),
            daemon=True
        )
        self.creation_thread.start()
        
        # 定期检查线程状态
        self.check_thread_status()
    
    def run_creation(self, img_paths, output_path, duration, music, text):
        """在后台线程中运行创建过程"""
        try:
            # 进度更新回调函数
            def progress_callback(current, total, phase=None):
                # 分阶段更新进度条
                if phase == "processing":
                    # 图片处理阶段占总进度的70%
                    progress = min(70.0, current / total * 70.0)
                    self.progress_var.set(progress)
                    self.update_progress_label(progress, 100)
                elif phase == "compiling":
                    # 视频合成阶段占总进度的30%，从70%开始
                    progress = min(100.0, 70.0 + (current / 100.0) * 30.0)
                    self.progress_var.set(progress)
                    self.update_progress_label(progress, 100)
            
            # 保存图片列表到临时文件
            with open("temp_img_list.txt", "w") as f:
                f.write("\n".join(img_paths))
            
            # 创建视频（传递进度回调）
            success = create_video(img_paths, output_path, duration, music, progress_callback)
            
            # 完成后更新状态
            if success:
                print("\n视频创建完成!")
                self.progress_var.set(100)
                self.update_progress_label(100, 100)
                messagebox.showinfo("成功", f"视频已成功创建: {os.path.abspath(output_path)}")
            else:
                messagebox.showerror("错误", "视频创建失败，请查看日志")
            
        except Exception as e:
            print(f"\n发生错误: {str(e)}")
            messagebox.showerror("错误", f"创建视频时出错: {str(e)}")
        
        finally:
            # 尝试删除临时文件
            try:
                os.remove("temp_img_list.txt")
            except:
                pass
            
            # 恢复UI状态
            self.create_btn.config(state=tk.NORMAL)
            self.update_progress_label(100, 100)
    
    def check_thread_status(self):
        """定期检查线程状态和更新进度"""
        if self.creation_thread and self.creation_thread.is_alive():
            # 每1秒检查一次
            self.root.after(1000, self.check_thread_status)
        else:
            # 线程完成
            pass
    
    def on_closing(self):
        """窗口关闭时的清理操作"""
        if self.creation_thread and self.creation_thread.is_alive():
            if messagebox.askokcancel("退出", "视频创建仍在进行中，确定要退出吗?"):
                # 设置停止标志（需要更多实现才能实际停止进程）
                self.stop_flag = True
                self.root.destroy()
        else:
            self.root.destroy()
        
        # 清理临时预览文件
        if self.temp_preview_path and os.path.exists(self.temp_preview_path):
            try:
                os.remove(self.temp_preview_path)
            except:
                pass
        
        # 恢复标准输出
        sys.stdout = self.original_stdout

def main():
    """应用程序入口"""
    root = tk.Tk()
    app = VideoCreatorApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()

if __name__ == "__main__":
    main()