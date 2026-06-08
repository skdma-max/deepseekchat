"""
DeepSeekChat — 完整版 Agent 桌面应用
能力：聊天 · 子代理并行 · 上下文压缩 · 沙箱隔离 · Git · Grep · Find · Shell · Python · Write · Search · Test · 会话管理 · 工作流程 · 持久化任务
"""
import sys, os, json, re, threading, time, subprocess, urllib.request, urllib.parse, tempfile, shutil, traceback, uuid
import tkinter as tk
from tkinter import messagebox
from pathlib import Path
from collections import deque

# ── 跨平台字体 ──
def _get_platform_fonts():
    """返回当前平台的推荐中文字体列表（第一个可用的将被使用）"""
    if sys.platform.startswith("win"):
        return ["Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "SimSun", "TkDefaultFont"]
    elif sys.platform.startswith("darwin"):
        return ["PingFang SC", "Heiti SC", "STHeiti", "Apple LiSung", "TkDefaultFont"]
    else:  # Linux
        return ["Noto Sans CJK SC", "WenQuanYi Micro Hei", "WenQuanYi Zen Hei",
                "Noto Sans SC", "Source Han Sans SC", "Sans", "TkDefaultFont"]

def _get_best_font(family_list, size, bold=False, italic=False):
    """从列表中选取第一个实际可用的字体，否则回退到系统默认"""
    import tkinter.font as tkfont
    available = set(tkfont.families())
    for f in family_list:
        if f in available:
            sl = ""
            if bold: sl += " bold"
            if italic: sl += " italic"
            return (f, size) + (sl.strip(),) if sl else (f, size)
    # 回退
    sl = ""
    if bold: sl += " bold"
    if italic: sl += " italic"
    return ("TkDefaultFont", size) + (sl.strip(),) if sl else ("TkDefaultFont", size)

_FONT_LIST = _get_platform_fonts()

# ── 依赖 ──
MISSING=[]
try:import tomllib
except ImportError:
    try:import tomli as tomllib
    except ImportError:MISSING.append("tomli")
try:from openai import OpenAI
except ImportError:MISSING.append("openai")
if MISSING:
    PY=sys.executable
    for p in MISSING:subprocess.run([PY,"-m","pip","install",p],capture_output=True)
    try:import tomllib
    except ImportError:import tomli as tomllib
    from openai import OpenAI

# ── 配置 ──
APP_DIR=Path(__file__).parent
TASKS_FILE=APP_DIR/"app_tasks.json"
SESSIONS_DIR=APP_DIR/"sessions"
MODEL_NAME="deepseek-chat"
MODE_DEFAULT="Agent"
PRICE_INPUT=1.0;PRICE_OUTPUT=2.0
MAX_FILE_KB=500;MAX_AUTO_ROUNDS=10;TOOL_TIMEOUT=300
COMPRESS_THRESHOLD=20      # 超过此轮数自动压缩
MAX_SUBAGENTS=5            # 最大并行子代理
SUBAGENT_TIMEOUT=180       # 子代理超时（秒）

MODEL_MAP={"Flash":"deepseek-chat","Pro":"deepseek-chat"}
_CONFIG_TO_DISPLAY={"deepseek-v4-pro":"Pro","deepseek-v4-flash":"Flash","deepseek-chat":"Pro","deepseek-reasoner":"Pro"}
MODEL_OPTIONS=list(MODEL_MAP.keys());MODE_OPTIONS=["Agent","Plan"]

# ── 标记 ──
TAG_TASK=re.compile(r'<!--task:\s*(.+?)-->',re.I)
TAG_DONE=re.compile(r'<!--done:\s*(.+?)-->',re.I)
TAG_PROG=re.compile(r'<!--progress:\s*(\d+)-->',re.I)
TAG_ATASK=re.compile(r'<!--atask:\s*(.+?)-->',re.I)
TAG_ADONE=re.compile(r'<!--adone:\s*(.+?)-->',re.I)
TAG_READ=re.compile(r'\[READ:\s*(.+?)\]',re.I)
TAG_RUN=re.compile(r'\[RUN:\s*(.+?)\]',re.I)
TAG_SEARCH=re.compile(r'\[SEARCH:\s*(.+?)\]',re.I)
TAG_GIT=re.compile(r'\[GIT:\s*(.+?)\]',re.I)
TAG_GREP=re.compile(r'\[GREP:\s*(.+?)\]',re.I)
TAG_FIND=re.compile(r'\[FIND:\s*(.+?)\]',re.I)
TAG_WRITE=re.compile(r'\[WRITE:\s*(.+?)\](.+?)\[/WRITE\]',re.I|re.S)
TAG_PY=re.compile(r'\[PY:\s*(.+?)\]',re.I)
TAG_AGENT=re.compile(r'\[AGENT:\s*(.+?)\]',re.I)

# ── 持久化 ──
def _load_tf():
    if TASKS_FILE.exists():
        try:return json.loads(TASKS_FILE.read_text(encoding="utf-8"))
        except:pass
    return[]
def _save_tf(t):TASKS_FILE.write_text(json.dumps(t,ensure_ascii=False,indent=2),encoding="utf-8")
def load_config():
    for p in[Path.home()/".codewhale"/"config.toml",APP_DIR/"config.toml",Path(sys.executable).parent/"config.toml"if getattr(sys,'frozen',False)else None]:
        if p and p.exists():
            try:
                d=tomllib.load(open(p,"rb"))
                k=d.get("api_key")or d.get("providers",{}).get("deepseek",{}).get("api_key")
                if k:raw=d.get("default_text_model")or"Pro";return k,_CONFIG_TO_DISPLAY.get(raw,"Pro")
            except:pass
    return None,"Pro"
def _sc(k):(APP_DIR/"config.json").write_text(json.dumps({"api_key":k},ensure_ascii=False))

def _rc(cmd,cwd,timeout=TOOL_TIMEOUT):
    try:
        r=subprocess.run(cmd,shell=True,capture_output=True,text=True,cwd=cwd,timeout=timeout,encoding="utf-8",errors="replace")
        o=r.stdout.strip();e=r.stderr.strip()
        p=[]
        if o:p.append(o)
        if e:p.append(f"[stderr]\n{e}")
        return"\n".join(p)if p else f"(exit {r.returncode})"
    except subprocess.TimeoutExpired:return f"[超时 {timeout}s]"
    except Exception as ex:return f"[错误: {ex}]"

def _rp(path,wd):
    p=Path(path.strip().strip('"').strip("'"))
    if not p.is_absolute():p=Path(wd)/p
    return p

# ════════════════ 子代理 ════════════════
class SubAgent:
    def __init__(self, task, api_key, api_model, work_dir, on_update, on_done):
        self.id=str(uuid.uuid4())[:8]
        self.task=task;self.status="running";self.result="";self.start_time=time.time()
        self.api_key=api_key;self.api_model=api_model;self.work_dir=work_dir
        self.on_update=on_update;self.on_done=on_done
        self.messages=[
            {"role":"system","content":f"你是子代理 #{self.id}。完成任务后简洁报告结果。工作目录:{work_dir}\n可用工具:[READ:路径][RUN:命令][GREP:模式][FIND:文件名]"},
            {"role":"user","content":task}
        ]
        self._thread=threading.Thread(target=self._run,daemon=True)
        self._thread.start()

    def _run(self):
        try:
            client=OpenAI(api_key=self.api_key,base_url="https://api.deepseek.com")
            stream=client.chat.completions.create(model=self.api_model,messages=self.messages,stream=True,temperature=0.5,max_tokens=2048)
            full=""
            for chunk in stream:
                d=chunk.choices[0].delta
                if d.content:full+=d.content;self.on_update(self.id,full)
            self.result=full
            # 执行子代理请求的工具
            tools=self._extract(full)
            for t in tools:
                r=self._exec_tool(t);self.result+=f"\n[工具: {r[:500]}]"
            self.status="done"
        except Exception as e:self.result=str(e);self.status="failed"
        self.on_done(self)

    def _extract(self,text):
        ts=[]
        for m in re.finditer(r'\[READ:\s*(.+?)\]',text,re.I):ts.append(("READ",m.group(1).strip()))
        for m in re.finditer(r'\[RUN:\s*(.+?)\]',text,re.I):ts.append(("RUN",m.group(1).strip()))
        for m in re.finditer(r'\[GREP:\s*(.+?)\]',text,re.I):ts.append(("GREP",m.group(1).strip()))
        return ts[:3]

    def _exec_tool(self,t):
        try:
            if t[0]=="READ":
                p=_rp(t[1],self.work_dir)
                if p.exists()and p.is_file():return f"文件 {p.name}:\n{p.read_text(encoding='utf-8',errors='replace')[:2000]}"
                return f"READ: 无法读取 {t[1]}"
            elif t[0]=="RUN":return _rc(t[1],self.work_dir,30)
            elif t[0]=="GREP":
                p=_rp(".",self.work_dir)
                try:
                    reg=re.compile(t[1],re.I);rs=[]
                    for f in p.rglob("*"):
                        if f.is_file()and f.suffix in('.py','.md','.txt','.json','.yaml'):
                            try:
                                for i,l in enumerate(f.read_text(encoding="utf-8",errors="replace").splitlines(),1):
                                    if reg.search(l):rs.append(f"{f}:{i}: {l.strip()[:100]}")
                                    if len(rs)>=10:break
                            except:pass
                        if len(rs)>=10:break
                    return"\n".join(rs)if rs else"无匹配"
                except:return"GREP 失败"
        except:return str(traceback.format_exc())
        return""

    def cancel(self):self.status="cancelled"


# ════════════════ 主应用 ════════════════
class DeepSeekChatApp:
    C={"bg_main":"#1e1e2e","bg_chat":"#181825","bg_input":"#11111b","bg_panel":"#161622","bg_status":"#11111b","bg_bar":"#1a1a2e","panel_hdr":"#1a1a2e","text_1":"#cdd6f4","text_2":"#a6adc8","accent":"#89b4fa","accent2":"#a6e3a1","border":"#45475a","task_done":"#585b70","prog_bg":"#313244","prog_fg":"#a6e3a1","tool_out":"#f9e2af","tool_err":"#f38ba8","agent":"#cba6f7"}
    FN=_get_best_font(_FONT_LIST,11);FS=_get_best_font(_FONT_LIST,10);FT=_get_best_font(_FONT_LIST,9)
    FB=_get_best_font(_FONT_LIST,11,bold=True);FTi=_get_best_font(_FONT_LIST,13,bold=True)

    def __init__(self):
        self.root=tk.Tk();self.root.title("DeepSeekChat");self.root.geometry("1100x680");self.root.minsize(800,500)
        self.root.configure(bg=self.C["bg_main"])
        self.api_key,self.display_model=load_config()
        self.client=OpenAI(api_key=self.api_key,base_url="https://api.deepseek.com")if self.api_key else None
        self.api_model=MODEL_NAME;self.mode=MODE_DEFAULT;self.work_dir=str(APP_DIR)
        self.base_system=(
            "你是全自动 Agent。可用工具标记：\n"
            "  [READ:路径] [WRITE:路径]内容[/WRITE] [RUN:命令] [PY:代码]\n"
            "  [GIT:操作] [GREP:模式] [FIND:文件名] [SEARCH:关键词]\n"
            "  [AGENT:任务描述]  —— 创建子代理后台并行执行\n"
            "多步骤任务依次用标记。还可管理界面：\n"
            "  <!--task:步骤--> <!--done:步骤--> <!--progress:N--> <!--atask:标题--> <!--adone:标题-->\n"
            f"工作目录: {self.work_dir}\n"
        )
        self.messages=[{"role":"system","content":self.base_system}]
        self.tasks=[];self._tid=0;self.progress_pct=0
        self.app_tasks=_load_tf();self._atid=max((t["id"]for t in self.app_tasks),default=0)+1
        self.in_tok=0;self.out_tok=0;self.cost=0.0;self.turns=0
        self.panel_tab="workflow";self.announcement_visible=True;self.auto_rounds=0
        self.subagents={};self._pending_agent_tasks=[]   # 子代理队列
        SESSIONS_DIR.mkdir(exist_ok=True)
        self.setup_ui()
        if not self.api_key:self._show_setup_dialog()
        self.root.protocol("WM_DELETE_WINDOW",self.on_close)
        self.root.bind("<Control-Return>",lambda e:self.send_message())

    # ── 设置 ──
    def _show_setup_dialog(self):
        d=tk.Toplevel(self.root);d.title("设置 API Key");d.geometry("460x220");d.configure(bg="#1e1e2e");d.transient(self.root);d.grab_set();d.resizable(False,False)
        tk.Label(d,text="🔑 请输入 DeepSeek API Key",font=self.FTi,fg=self.C["accent"],bg="#1e1e2e").pack(pady=(20,8))
        e=tk.Entry(d,font=self.FN,width=50,show="•",bg="#313244",fg="#cdd6f4",insertbackground="#cdd6f4",relief="flat",highlightthickness=1,highlightbackground="#45475a")
        e.pack(pady=12,ipady=6);e.focus()
        bf=tk.Frame(d,bg="#1e1e2e");bf.pack()
        def sv():
            k=e.get().strip()
            if k:self.api_key=k;self.client=OpenAI(api_key=k,base_url="https://api.deepseek.com");_sc(k);d.destroy()
            else:messagebox.showwarning("提示","请输入有效的 API Key")
        tk.Button(bf,text="保 存",command=sv,bg="#3b82f6",fg="white",font=self.FB,relief="flat",padx=30,pady=6,cursor="hand2").pack(side=tk.LEFT,padx=4)
        tk.Button(bf,text="退 出",command=self.root.destroy,bg="#45475a",fg="#cdd6f4",font=self.FB,relief="flat",padx=30,pady=6,cursor="hand2").pack(side=tk.LEFT,padx=4)

    # ════════════════ UI ════════════════
    def setup_ui(self):
        hdr=tk.Frame(self.root,bg=self.C["bg_main"],height=38);hdr.pack(fill=tk.X,padx=16,pady=(8,0));hdr.pack_propagate(False)
        tk.Label(hdr,text="🐋 DeepSeekChat",font=self.FTi,fg=self.C["accent"],bg=self.C["bg_main"]).pack(side=tk.LEFT)
        rg=tk.Frame(hdr,bg=self.C["bg_main"]);rg.pack(side=tk.RIGHT)
        mb=tk.Frame(rg,bg=self.C["border"],padx=1,pady=1);mb.pack(side=tk.RIGHT,padx=(6,0))
        self.mode_label=tk.Label(mb,text=self.mode,font=self.FT,fg=self.C["accent"],bg="#2a2a3e",padx=8,pady=1,cursor="hand2");self.mode_label.pack();self.mode_label.bind("<Button-1>",lambda e:self._show_mode_menu(e))
        vb=tk.Frame(rg,bg=self.C["border"],padx=1,pady=1);vb.pack(side=tk.RIGHT,padx=(6,0))
        self.model_label=tk.Label(vb,text=self.display_model,font=self.FT,fg=self.C["accent2"],bg="#2a2a3e",padx=8,pady=1,cursor="hand2");self.model_label.pack();self.model_label.bind("<Button-1>",lambda e:self._show_model_menu(e))
        self.status_light=tk.Label(rg,text="●",font=_get_best_font(_FONT_LIST,12),fg="#a6e3a1",bg=self.C["bg_main"]);self.status_light.pack(side=tk.RIGHT,padx=(6,2))
        tk.Frame(self.root,bg=self.C["border"],height=1).pack(fill=tk.X,padx=12,pady=2)
        self._build_bar()
        self.pane=tk.PanedWindow(self.root,orient=tk.HORIZONTAL,bg=self.C["border"],sashwidth=4,sashrelief=tk.FLAT);self.pane.pack(fill=tk.BOTH,expand=True,padx=12)
        self.panel_frame=tk.Frame(self.pane,bg=self.C["bg_panel"],width=280);self._build_side();self.pane.add(self.panel_frame,minsize=220)
        right=tk.Frame(self.pane,bg=self.C["bg_chat"]);self.pane.add(right,minsize=400)
        cf=tk.Frame(right,bg=self.C["bg_chat"]);cf.pack(fill=tk.BOTH,expand=True)
        self.chat_text=tk.Text(cf,wrap=tk.WORD,font=self.FN,bg=self.C["bg_chat"],fg=self.C["text_1"],relief="flat",bd=0,padx=16,pady=12,state=tk.DISABLED,cursor="arrow");self.chat_text.pack(side=tk.LEFT,fill=tk.BOTH,expand=True)
        sb=tk.Scrollbar(cf,bg=self.C["bg_chat"],troughcolor=self.C["bg_input"]);sb.pack(side=tk.RIGHT,fill=tk.Y)
        self.chat_text.configure(yscrollcommand=sb.set);sb.configure(command=self.chat_text.yview)
        for tag,fg in[("user_label","#60a5fa"),("ai_label","#a6e3a1"),("user_msg","#bfdbfe"),("ai_msg","#cdd6f4"),("system_msg","#6c7086"),("tool_out","#f9e2af"),("tool_err","#f38ba8"),("agent_msg","#cba6f7")]:
            kw={"foreground":fg}
            if"label"in tag:kw["font"]=self.FB
            if"system"in tag:kw.update(font=_get_best_font(_FONT_LIST,9,italic=True),justify=tk.CENTER)
            self.chat_text.tag_configure(tag,**kw)
        inf=tk.Frame(right,bg=self.C["bg_input"]);inf.pack(fill=tk.X,padx=0,pady=(4,0))
        self.input_text=tk.Text(inf,height=3,font=self.FN,bg=self.C["bg_input"],fg=self.C["text_1"],insertbackground=self.C["accent"],relief="flat",bd=0,padx=12,pady=10,wrap=tk.WORD);self.input_text.pack(side=tk.LEFT,fill=tk.X,expand=True,padx=(0,8))
        self.input_text.bind("<Return>",self._on_enter);self.input_text.bind("<Shift-Return>",self._on_shift_enter)
        self.send_btn=tk.Button(inf,text="发送",command=self.send_message,bg=self.C["accent"],fg="#1e1e2e",font=self.FB,relief="flat",padx=18,pady=6,cursor="hand2");self.send_btn.pack(side=tk.RIGHT,pady=4)
        self._build_status_bar()
        self._append_system("完整版 Agent 就绪 — 子代理 · 压缩 · 沙箱 · Git · Grep · /help")

    # ── 公告栏 ──
    def _build_bar(self):
        self.bar_frame=tk.Frame(self.root,bg=self.C["bg_bar"],height=30);self.bar_frame.pack(fill=tk.X,padx=12,pady=(0,2));self.bar_frame.pack_propagate(False)
        self.bar_text=tk.Label(self.bar_frame,text="💡 /agent <任务> 创建子代理  |  /read /write /run /py /grep /git /search /test /save /load /compress",font=self.FT,fg=self.C["text_2"],bg=self.C["bg_bar"],anchor="w");self.bar_text.pack(side=tk.LEFT,padx=10,fill=tk.X,expand=True)
        sc=tk.Label(self.bar_frame,text="⌨️ 快捷键",font=self.FT,fg=self.C["accent"],bg=self.C["bg_bar"],cursor="hand2",padx=6);sc.pack(side=tk.RIGHT,padx=2);sc.bind("<Button-1>",lambda e:self._show_sc())
        x=tk.Label(self.bar_frame,text="✕",font=self.FT,fg=self.C["text_2"],bg=self.C["bg_bar"],cursor="hand2",padx=6);x.pack(side=tk.RIGHT,padx=(0,4));x.bind("<Button-1>",lambda e:self._toggle_bar())
    def _toggle_bar(self):
        if self.announcement_visible:self.bar_frame.pack_forget();self.announcement_visible=False
        else:self.bar_frame.pack(fill=tk.X,padx=12,pady=(0,2),before=self.pane);self.announcement_visible=True

    def _show_sc(self):
        tabs_data={
            "🌐 全局":[("F1/Ctrl-/","帮助"),("Ctrl-K","命令面板"),("Ctrl-C","取消/关闭"),("Ctrl-D","退出"),("Tab","切换模式 Plan→Agent→YOLO"),("Shift-Tab","推理深度"),("Ctrl-R","恢复会话"),("Ctrl-L","刷新"),("Ctrl-O","活动详情"),("Ctrl-Shift-E","文件树"),("Alt-G","顶部"),("Alt-1~5","侧边栏"),("Ctrl-Alt-0","隐藏右侧"),("Esc","关闭")],
            "✏️ 编辑器":[("Enter","发送"),("Alt-Enter/Ctrl-J","换行"),("Ctrl-U","删行首"),("Ctrl-W","删词"),("Ctrl-A/Home","行首"),("Ctrl-E/End","行尾"),("Ctrl-←/→","跳词"),("Ctrl-V","粘贴"),("Ctrl-Y","粘贴"),("↑/↓","历史"),("Ctrl-P/N","历史"),("Ctrl-S","暂存"),("Alt-R","搜索历史"),("Tab","补全"),("@","提及")],
            "📜 转录区":[("↑/↓/j/k","滚动"),("PgUp/PgDn","翻页"),("Home/g","顶部"),("End/G","底部"),("Esc","回编辑"),("y","复制"),("v","选择"),("o","打开链接")],
            "📂 侧边栏":[("↑/↓/j/k","移动"),("Enter","激活"),("Tab","切换面板"),("Esc","回编辑")],
            "/ 命令 ":[("/read","读文件/列目录"),("/write","弹出编辑器写文件"),("/run","执行Shell(沙箱)"),("/py","执行Python"),("/grep","正则搜索代码"),("/find","搜索文件名"),("/git","Git操作"),("/search","Web搜索"),("/test","运行测试"),("/agent","创建子代理"),("/agents","查看代理"),("/compress","压缩上下文"),("/save","保存会话"),("/load","加载会话"),("/list","列出会话"),("/help","帮助")],
            "💬 会话":[("↑/↓/j/k","移动"),("1-9","快速打开"),("PgUp/PgDn","翻页"),("Enter","恢复"),("/","搜索"),("s","排序"),("a","切换范围"),("d","删除"),("Esc/q","关闭")],
            "✅ 审批":[("y/Y","批准一次"),("a/A","全部批准"),("n/N/Esc","拒绝"),("e","编辑后执行")],
        }
        tabs=list(tabs_data.keys());self._sc_data=tabs_data
        dlg=tk.Toplevel(self.root);dlg.title("快捷键参考");dlg.geometry("660x520");dlg.minsize(550,400);dlg.configure(bg=self.C["bg_main"]);dlg.transient(self.root);dlg.grab_set()
        tf=tk.Frame(dlg,bg=self.C["bg_main"]);tf.pack(fill=tk.X,padx=12,pady=(12,0))
        self._st={};self._stn=tabs[0]
        rfs=[tk.Frame(tf,bg=self.C["bg_main"])for _ in range(2)];rfs[0].pack(fill=tk.X);rfs[1].pack(fill=tk.X,pady=(2,0))
        for i,n in enumerate(tabs):
            rf=rfs[i//4];lbl=tk.Label(rf,text=n,font=self.FT,fg=self.C["accent"]if i==0 else self.C["text_2"],bg=self.C["bg_bar"],padx=8,pady=3,cursor="hand2")
            lbl.pack(side=tk.LEFT,padx=1);lbl.bind("<Button-1>",lambda e,n=n:self._sw_st(dlg,n));self._st[n]=lbl
        tk.Frame(dlg,bg=self.C["border"],height=1).pack(fill=tk.X,padx=12,pady=4)
        self._stext=tk.Text(dlg,wrap=tk.WORD,font=self.FS,bg=self.C["bg_chat"],fg=self.C["text_1"],relief="flat",bd=0,padx=16,pady=12,state=tk.DISABLED,cursor="arrow");self._stext.pack(fill=tk.BOTH,expand=True,padx=12,pady=(0,4))
        self._stext.tag_configure("key",foreground=self.C["accent2"],font=_get_best_font(_FONT_LIST,10,bold=True));self._stext.tag_configure("desc",foreground=self.C["text_1"])
        self._fl_st(tabs[0])
        tk.Button(dlg,text="关闭",command=dlg.destroy,bg=self.C["border"],fg=self.C["text_1"],font=self.FS,relief="flat",padx=20,pady=4,cursor="hand2").pack(pady=(0,10))
    def _sw_st(self,dlg,name):
        self._stn=name
        for n,lbl in self._st.items():lbl.configure(fg=self.C["accent"]if n==name else self.C["text_2"])
        self._fl_st(name)
    def _fl_st(self,name):
        items=self._sc_data.get(name,[]);self._stext.configure(state=tk.NORMAL);self._stext.delete("1.0",tk.END)
        for k,d in items:self._stext.insert(tk.END,k,"key");self._stext.insert(tk.END,"  →  "+d+"\n","desc")
        self._stext.configure(state=tk.DISABLED)

    # ── 侧面板 ──
    def _build_side(self):
        tb=tk.Frame(self.panel_frame,bg=self.C["panel_hdr"],height=34);tb.pack(fill=tk.X);tb.pack_propagate(False)
        self.tb_wf=tk.Label(tb,text="📋 流程",font=self.FS,fg=self.C["accent"],bg=self.C["panel_hdr"],cursor="hand2",padx=6,pady=5);self.tb_wf.pack(side=tk.LEFT);self.tb_wf.bind("<Button-1>",lambda e:self._sw_p("workflow"))
        self.tb_ts=tk.Label(tb,text="📌 任务",font=self.FS,fg=self.C["text_2"],bg=self.C["panel_hdr"],cursor="hand2",padx=6,pady=5);self.tb_ts.pack(side=tk.LEFT);self.tb_ts.bind("<Button-1>",lambda e:self._sw_p("tasks"))
        self.tb_ag=tk.Label(tb,text="🤖 代理",font=self.FS,fg=self.C["text_2"],bg=self.C["panel_hdr"],cursor="hand2",padx=6,pady=5);self.tb_ag.pack(side=tk.LEFT);self.tb_ag.bind("<Button-1>",lambda e:self._sw_p("agents"))
        tk.Frame(self.panel_frame,bg=self.C["border"],height=1).pack(fill=tk.X)
        self.wfc=tk.Frame(self.panel_frame,bg=self.C["bg_panel"]);self._bld_wf();self.wfc.pack(fill=tk.BOTH,expand=True)
        self.tsc=tk.Frame(self.panel_frame,bg=self.C["bg_panel"]);self._bld_ts()
        self.agc=tk.Frame(self.panel_frame,bg=self.C["bg_panel"]);self._bld_ag()
    def _sw_p(self,tab):
        self.panel_tab=tab
        for f in[self.wfc,self.tsc,self.agc]:f.pack_forget()
        if tab=="workflow":self.wfc.pack(fill=tk.BOTH,expand=True)
        elif tab=="tasks":self.tsc.pack(fill=tk.BOTH,expand=True);self._rf_ts()
        else:self.agc.pack(fill=tk.BOTH,expand=True);self._rf_ag()
        for l,t in[(self.tb_wf,"workflow"),(self.tb_ts,"tasks"),(self.tb_ag,"agents")]:
            l.configure(fg=self.C["accent"]if t==tab else self.C["text_2"])

    def _bld_wf(self):
        c=self.wfc;hf=tk.Frame(c,bg=self.C["bg_panel"]);hf.pack(fill=tk.X,padx=8,pady=(6,2))
        self.wf_cnt=tk.Label(hf,text="0/0",font=self.FS,fg=self.C["text_2"],bg=self.C["bg_panel"]);self.wf_cnt.pack(side=tk.RIGHT)
        lc=tk.Frame(c,bg=self.C["bg_panel"]);lc.pack(fill=tk.BOTH,expand=True)
        self.wf_cv=tk.Canvas(lc,bg=self.C["bg_panel"],highlightthickness=0,bd=0);self.wf_cv.pack(side=tk.LEFT,fill=tk.BOTH,expand=True)
        ws=tk.Scrollbar(lc,orient=tk.VERTICAL,bg=self.C["bg_panel"],troughcolor=self.C["bg_input"]);ws.pack(side=tk.RIGHT,fill=tk.Y);self.wf_cv.configure(yscrollcommand=ws.set);ws.configure(command=self.wf_cv.yview)
        self.wf_in=tk.Frame(self.wf_cv,bg=self.C["bg_panel"]);self.wf_in_id=self.wf_cv.create_window((0,0),window=self.wf_in,anchor="nw")
        self.wf_in.bind("<Configure>",lambda e:self.wf_cv.configure(scrollregion=self.wf_cv.bbox("all")))
        self.wf_cv.bind("<Configure>",lambda e:self.wf_cv.itemconfig(self.wf_in_id,width=e.width))
        self.wf_cv.bind_all("<MouseWheel>",self._mw)
        bf=tk.Frame(c,bg=self.C["bg_panel"]);bf.pack(fill=tk.X,side=tk.BOTTOM,padx=10,pady=(4,8))
        self.pg_cv=tk.Canvas(bf,height=18,bg=self.C["prog_bg"],highlightthickness=0,bd=0);self.pg_cv.pack(fill=tk.X,pady=(0,4))
        self.pg_lb=tk.Label(bf,text="0%",font=self.FS,fg=self.C["text_2"],bg=self.C["bg_panel"]);self.pg_lb.pack()
        af=tk.Frame(c,bg=self.C["bg_panel"]);af.pack(fill=tk.X,side=tk.BOTTOM,padx=10,pady=(0,10))
        self.ae=tk.Entry(af,font=self.FS,bg=self.C["bg_input"],fg=self.C["text_1"],insertbackground=self.C["accent"],relief="flat",bd=0);self.ae.pack(side=tk.LEFT,fill=tk.X,expand=True,ipady=4,padx=(0,6));self.ae.bind("<Return>",lambda e:self._add_tm())
        tk.Button(af,text="＋",command=self._add_tm,bg=self.C["accent2"],fg="#1e1e2e",font=_get_best_font(_FONT_LIST,12,bold=True),relief="flat",padx=8,cursor="hand2").pack(side=tk.RIGHT)
        self._rf_wf();self._draw_pg()
    def _mw(self,e):
        w=e.widget
        while w:
            if w in(self.wf_cv,self.ts_cv,self.ag_cv):w.yview_scroll(int(-1*(e.delta/120)),"units");return
            w=w.master

    def _bld_ts(self):
        c=self.tsc;hf=tk.Frame(c,bg=self.C["bg_panel"]);hf.pack(fill=tk.X,padx=8,pady=(6,2))
        self.at_cnt=tk.Label(hf,text="",font=self.FS,fg=self.C["text_2"],bg=self.C["bg_panel"]);self.at_cnt.pack(side=tk.RIGHT)
        lc=tk.Frame(c,bg=self.C["bg_panel"]);lc.pack(fill=tk.BOTH,expand=True)
        self.ts_cv=tk.Canvas(lc,bg=self.C["bg_panel"],highlightthickness=0,bd=0);self.ts_cv.pack(side=tk.LEFT,fill=tk.BOTH,expand=True)
        ts=tk.Scrollbar(lc,orient=tk.VERTICAL,bg=self.C["bg_panel"],troughcolor=self.C["bg_input"]);ts.pack(side=tk.RIGHT,fill=tk.Y);self.ts_cv.configure(yscrollcommand=ts.set);ts.configure(command=self.ts_cv.yview)
        self.ts_in=tk.Frame(self.ts_cv,bg=self.C["bg_panel"]);self.ts_in_id=self.ts_cv.create_window((0,0),window=self.ts_in,anchor="nw")
        self.ts_in.bind("<Configure>",lambda e:self.ts_cv.configure(scrollregion=self.ts_cv.bbox("all")))
        self.ts_cv.bind("<Configure>",lambda e:self.ts_cv.itemconfig(self.ts_in_id,width=e.width))
        af=tk.Frame(c,bg=self.C["bg_panel"]);af.pack(fill=tk.X,side=tk.BOTTOM,padx=10,pady=(0,4))
        tk.Button(af,text="＋ 新建任务",command=self._new_at,bg=self.C["accent2"],fg="#1e1e2e",font=self.FS,relief="flat",padx=8,pady=4,cursor="hand2").pack(fill=tk.X)
        self._rf_ts()

    def _bld_ag(self):
        c=self.agc;hf=tk.Frame(c,bg=self.C["bg_panel"]);hf.pack(fill=tk.X,padx=8,pady=(6,2))
        self.ag_cnt=tk.Label(hf,text="",font=self.FS,fg=self.C["text_2"],bg=self.C["bg_panel"]);self.ag_cnt.pack(side=tk.RIGHT)
        lc=tk.Frame(c,bg=self.C["bg_panel"]);lc.pack(fill=tk.BOTH,expand=True)
        self.ag_cv=tk.Canvas(lc,bg=self.C["bg_panel"],highlightthickness=0,bd=0);self.ag_cv.pack(side=tk.LEFT,fill=tk.BOTH,expand=True)
        ags=tk.Scrollbar(lc,orient=tk.VERTICAL,bg=self.C["bg_panel"],troughcolor=self.C["bg_input"]);ags.pack(side=tk.RIGHT,fill=tk.Y);self.ag_cv.configure(yscrollcommand=ags.set);ags.configure(command=self.ag_cv.yview)
        self.ag_in=tk.Frame(self.ag_cv,bg=self.C["bg_panel"]);self.ag_in_id=self.ag_cv.create_window((0,0),window=self.ag_in,anchor="nw")
        self.ag_in.bind("<Configure>",lambda e:self.ag_cv.configure(scrollregion=self.ag_cv.bbox("all")))
        self.ag_cv.bind("<Configure>",lambda e:self.ag_cv.itemconfig(self.ag_in_id,width=e.width))
        af=tk.Frame(c,bg=self.C["bg_panel"]);af.pack(fill=tk.X,side=tk.BOTTOM,padx=10,pady=(0,4))
        tk.Button(af,text="取消所有代理",command=self._cancel_all_agents,bg=self.C["tool_err"],fg="#1e1e2e",font=self.FS,relief="flat",padx=8,pady=4,cursor="hand2").pack(fill=tk.X)

    # ── 子代理面板 ──
    def _on_agent_update(self,aid,partial):
        self.root.after(0,lambda:self._rf_ag())
    def _on_agent_done(self,agent):
        self.root.after(0,lambda:self._handle_agent_done(agent))
    def _handle_agent_done(self,agent):
        self._append_system(f"🤖 子代理 #{agent.id} [{agent.status}]: {agent.task}\n结果: {agent.result[:1000]}","agent_msg")
        if len(agent.result)>1000:self._append_system("... (截断)");self._rf_ag()
    def _cancel_all_agents(self):
        for a in self.subagents.values():
            if a.status=="running":a.cancel()
        self._rf_ag()
    def _rf_ag(self):
        for w in self.ag_in.winfo_children():w.destroy()
        if not self.subagents:tk.Label(self.ag_in,text="（无活跃子代理）",font=self.FS,fg=self.C["task_done"],bg=self.C["bg_panel"]).pack(pady=20)
        else:
            for a in sorted(self.subagents.values(),key=lambda x:x.start_time,reverse=True):
                r=tk.Frame(self.ag_in,bg=self.C["bg_panel"]);r.pack(fill=tk.X,padx=8,pady=2)
                st_icon={"running":"◉","done":"●","failed":"✕","cancelled":"○"}.get(a.status,"○")
                st_clr={"running":self.C["accent"],"done":self.C["accent2"],"failed":self.C["tool_err"],"cancelled":self.C["task_done"]}.get(a.status,self.C["text_2"])
                tk.Label(r,text=st_icon,font=_get_best_font(_FONT_LIST,10),fg=st_clr,bg=self.C["bg_panel"],padx=2).pack(side=tk.LEFT)
                cf=tk.Frame(r,bg=self.C["bg_panel"]);cf.pack(side=tk.LEFT,fill=tk.X,expand=True,padx=4)
                tk.Label(cf,text=f"#{a.id} {a.task[:40]}",font=_get_best_font(_FONT_LIST,9),fg=self.C["text_1"],bg=self.C["bg_panel"],anchor="w").pack(anchor="w")
                elapsed=int(time.time()-a.start_time)
                tk.Label(cf,text=f"{a.status} · {elapsed}s",font=self.FT,fg=self.C["text_2"],bg=self.C["bg_panel"]).pack(anchor="w")
        rn=sum(1 for a in self.subagents.values()if a.status=="running")
        self.ag_cnt.configure(text=f"活跃 {rn}")

    # ── 工作流程方法 ──
    def _add_tm(self):t=self.ae.get().strip();t and self._add_t(t);self.ae.delete(0,tk.END)
    def _add_t(self,text):
        for t in self.tasks:
            if t["text"]==text:return
        self._tid+=1;self.tasks.append({"id":self._tid,"text":text,"done":False});self._rf_wf();self._up_pg()
    def _tg_t(self,tid):
        for t in self.tasks:
            if t["id"]==tid:t["done"]=not t["done"];break
        self._rf_wf();self._up_pg()
    def _dl_t(self,tid):self.tasks=[t for t in self.tasks if t["id"]!=tid];self._rf_wf();self._up_pg()
    def _dn_t(self,text):
        for t in self.tasks:
            if t["text"]==text and not t["done"]:t["done"]=True;self._rf_wf();self._up_pg();return
    def _rf_wf(self):
        for w in self.wf_in.winfo_children():w.destroy()
        if not self.tasks:tk.Label(self.wf_in,text="（暂无）",font=self.FS,fg=self.C["task_done"],bg=self.C["bg_panel"]).pack(pady=20)
        else:
            for t in self.tasks:self._mk_wr(t)
        d=sum(1 for t in self.tasks if t["done"]);self.wf_cnt.configure(text=f"{d}/{len(self.tasks)}")
    def _mk_wr(self,task):
        r=tk.Frame(self.wf_in,bg=self.C["bg_panel"]);r.pack(fill=tk.X,padx=8,pady=1)
        chk="☑"if task["done"]else"☐";cc=self.C["task_done"]if task["done"]else self.C["accent2"]
        c=tk.Label(r,text=chk,font=_get_best_font(_FONT_LIST,12),fg=cc,bg=self.C["bg_panel"],cursor="hand2");c.pack(side=tk.LEFT);c.bind("<Button-1>",lambda e,tid=task["id"]:self._tg_t(tid))
        fg=self.C["task_done"]if task["done"]else self.C["text_1"];fn=_get_best_font(_FONT_LIST,10,italic=True)if task["done"]else _get_best_font(_FONT_LIST,10)
        tk.Label(r,text=task["text"],font=fn,fg=fg,bg=self.C["bg_panel"],anchor="w").pack(side=tk.LEFT,fill=tk.X,expand=True,padx=(4,4))
        x=tk.Label(r,text="✕",font=_get_best_font(_FONT_LIST,9),fg=self.C["task_done"],bg=self.C["bg_panel"],cursor="hand2");x.pack(side=tk.RIGHT);x.bind("<Button-1>",lambda e,tid=task["id"]:self._dl_t(tid))

    # ── 持久化任务 ──
    def _new_at(self):
        d=tk.Toplevel(self.root);d.title("新建任务");d.geometry("420x160");d.configure(bg="#1e1e2e");d.transient(self.root);d.grab_set();d.resizable(False,False)
        tk.Label(d,text="📌 新建后台任务",font=self.FTi,fg=self.C["accent"],bg="#1e1e2e").pack(pady=(12,8))
        te=tk.Entry(d,font=self.FN,bg="#313244",fg="#cdd6f4",insertbackground="#cdd6f4",relief="flat",bd=0,highlightthickness=1,highlightbackground="#45475a");te.pack(fill=tk.X,padx=20,pady=(2,6),ipady=4);te.focus()
        bf=tk.Frame(d,bg="#1e1e2e");bf.pack()
        def cr():t=te.get().strip();t and self._ad_at(t);d.destroy()
        tk.Button(bf,text="创建",command=cr,bg=self.C["accent"],fg="#1e1e2e",font=self.FB,relief="flat",padx=20,pady=4,cursor="hand2").pack(side=tk.LEFT,padx=4)
        tk.Button(bf,text="取消",command=d.destroy,bg=self.C["border"],fg=self.C["text_1"],font=self.FN,relief="flat",padx=20,pady=4,cursor="hand2").pack(side=tk.LEFT,padx=4)
    def _ad_at(self,title):self._atid+=1;self.app_tasks.append({"id":self._atid,"title":title,"status":"pending","created_at":time.strftime("%Y-%m-%d %H:%M")});_save_tf(self.app_tasks);self._rf_ts()
    def _ad_at_m(self,title):
        for t in self.app_tasks:
            if t["title"]==title:return
        self._ad_at(title)
    def _tg_at(self,tid):
        for t in self.app_tasks:
            if t["id"]==tid:t["status"]="done"if t["status"]!="done"else"pending";break
        _save_tf(self.app_tasks);self._rf_ts()
    def _dl_at(self,tid):self.app_tasks=[t for t in self.app_tasks if t["id"]!=tid];_save_tf(self.app_tasks);self._rf_ts()
    def _dn_at(self,title):
        for t in self.app_tasks:
            if t["title"]==title and t["status"]!="done":t["status"]="done";_save_tf(self.app_tasks);self._rf_ts();return
    def _rf_ts(self):
        for w in self.ts_in.winfo_children():w.destroy()
        if not self.app_tasks:tk.Label(self.ts_in,text="（暂无）",font=self.FS,fg=self.C["task_done"],bg=self.C["bg_panel"]).pack(pady=30)
        else:
            for t in sorted(self.app_tasks,key=lambda x:x["id"],reverse=True):self._mk_ar(t)
        p=sum(1 for t in self.app_tasks if t["status"]=="pending");d=sum(1 for t in self.app_tasks if t["status"]=="done");self.at_cnt.configure(text=f"待办 {p} · 完成 {d}")
    def _mk_ar(self,task):
        r=tk.Frame(self.ts_in,bg=self.C["bg_panel"]);r.pack(fill=tk.X,padx=8,pady=2)
        im={"pending":("○",self.C["tool_out"]),"done":("●",self.C["accent2"])};ic,iclr=im.get(task["status"],("○",self.C["text_2"]))
        s=tk.Label(r,text=ic,font=_get_best_font(_FONT_LIST,10),fg=iclr,bg=self.C["bg_panel"],cursor="hand2",padx=2);s.pack(side=tk.LEFT);s.bind("<Button-1>",lambda e,tid=task["id"]:self._tg_at(tid))
        cf=tk.Frame(r,bg=self.C["bg_panel"]);cf.pack(side=tk.LEFT,fill=tk.X,expand=True,padx=(4,4))
        fg=self.C["task_done"]if task["status"]=="done"else self.C["text_1"];fn=_get_best_font(_FONT_LIST,10,italic=True)if task["status"]=="done"else _get_best_font(_FONT_LIST,10)
        tk.Label(cf,text=task["title"],font=fn,fg=fg,bg=self.C["bg_panel"],anchor="w").pack(anchor="w")
        tk.Label(cf,text=task.get("created_at",""),font=self.FT,fg=self.C["text_2"],bg=self.C["bg_panel"]).pack(anchor="w")
        x=tk.Label(r,text="✕",font=_get_best_font(_FONT_LIST,9),fg=self.C["task_done"],bg=self.C["bg_panel"],cursor="hand2");x.pack(side=tk.RIGHT);x.bind("<Button-1>",lambda e,tid=task["id"]:self._dl_at(tid))

    # ── 进度 ──
    def _up_pg(self):
        self.progress_pct=int(sum(1 for t in self.tasks if t["done"])/len(self.tasks)*100)if self.tasks else 0;self._draw_pg();self.pg_lb.configure(text=f"{self.progress_pct}%")
    def _st_pg(self,p):self.progress_pct=max(0,min(100,int(p)));self._draw_pg();self.pg_lb.configure(text=f"{self.progress_pct}%")
    def _draw_pg(self):
        w=self.pg_cv.winfo_width()
        if w<20:return
        self.pg_cv.delete("all");self.pg_cv.create_rectangle(0,0,w,18,fill=self.C["prog_bg"],outline="")
        fw=int(w*self.progress_pct/100)
        if fw>0:self.pg_cv.create_rectangle(0,0,fw,18,fill=self.C["prog_fg"],outline="")
        if fw>30:self.pg_cv.create_text(fw//2,9,text=f"{self.progress_pct}%",font=_get_best_font(_FONT_LIST,9,bold=True),fill="#181825")

    # ── 标记 ──
    def _proc_tags(self,text):
        c=text
        for m in TAG_PROG.finditer(text):self.root.after(0,lambda v=m.group(1):self._st_pg(v))
        c=TAG_PROG.sub("",c)
        for m in TAG_TASK.finditer(text):self.root.after(0,lambda v=m.group(1).strip():self._add_t(v))
        c=TAG_TASK.sub("",c)
        for m in TAG_DONE.finditer(text):self.root.after(0,lambda v=m.group(1).strip():self._dn_t(v))
        c=TAG_DONE.sub("",c)
        for m in TAG_ATASK.finditer(text):self.root.after(0,lambda v=m.group(1).strip():self._ad_at_m(v))
        c=TAG_ATASK.sub("",c)
        for m in TAG_ADONE.finditer(text):self.root.after(0,lambda v=m.group(1).strip():self._dn_at(v))
        c=TAG_ADONE.sub("",c)
        return c

    # ════════════════ 命令 ════════════════
    def _handle_cmd(self,cmd):
        p=cmd.split(maxsplit=1);op=p[0].lower();arg=p[1]if len(p)>1 else""
        m={
            "/read":lambda:self._cmd_read(arg),"/run":lambda:self._cmd_run(arg),
            "/search":lambda:self._cmd_search(arg),"/save":lambda:self._cmd_save(arg),
            "/load":lambda:self._cmd_load(arg),"/list":lambda:self._cmd_list(),
            "/help":lambda:self._cmd_help(),"/grep":lambda:self._cmd_grep(arg),
            "/find":lambda:self._cmd_find(arg),"/git":lambda:self._cmd_git(arg),
            "/write":lambda:self._cmd_write_prompt(arg),"/py":lambda:self._cmd_py(arg),
            "/test":lambda:self._cmd_test(),"/agent":lambda:self._cmd_agent(arg),
            "/agents":lambda:self._cmd_agents(),"/compress":lambda:self._cmd_compress(),
        }
        h=m.get(op);h()if h else self._append_system(f"未知命令: {op}，/help 查看帮助")

    def _cmd_read(self,path):
        p=_rp(path,self.work_dir)
        try:
            if not p.exists():self._append_system(f"❌ 不存在: {p}");return
            if p.is_dir():
                es=sorted(p.iterdir(),key=lambda x:(not x.is_dir(),x.name.lower()))[:100]
                lines=[f"📁 {p}/"]+[f"  {'📁'if e.is_dir()else'📄'} {e.name}"for e in es]
                self._append_system("\n".join(lines));return
            if p.stat().st_size>MAX_FILE_KB*1024:self._append_system(f"❌ 过大");return
            c=p.read_text(encoding="utf-8",errors="replace");self._append_system(f"📄 {p.name}:\n{c[:8000]}")
            if len(c)>8000:self._append_system(f"... (截断)")
        except Exception as e:self._append_system(f"❌ {e}")

    def _cmd_write_prompt(self,path):
        p=_rp(path,self.work_dir)
        d=tk.Toplevel(self.root);d.title(f"写入: {p.name}");d.geometry("600x400");d.configure(bg=self.C["bg_main"]);d.transient(self.root);d.grab_set()
        tk.Label(d,text=f"📝 {p}",font=self.FS,fg=self.C["accent2"],bg=self.C["bg_main"]).pack(pady=(10,4))
        te=tk.Text(d,font=self.FS,bg=self.C["bg_chat"],fg=self.C["text_1"],insertbackground=self.C["accent"],relief="flat",bd=0,padx=10,pady=10,wrap=tk.WORD);te.pack(fill=tk.BOTH,expand=True,padx=10,pady=4)
        if p.exists():
            try:te.insert("1.0",p.read_text(encoding="utf-8",errors="replace"))
            except:pass
        bf=tk.Frame(d,bg=self.C["bg_main"]);bf.pack(pady=(0,10))
        def do():
            c=te.get("1.0","end-1c")
            try:p.parent.mkdir(parents=True,exist_ok=True);p.write_text(c,encoding="utf-8");self._append_system(f"✅ 已写入: {p}");d.destroy()
            except Exception as e:self._append_system(f"❌ {e}")
        tk.Button(bf,text="保存",command=do,bg=self.C["accent2"],fg="#1e1e2e",font=self.FB,relief="flat",padx=20,pady=4,cursor="hand2").pack(side=tk.LEFT,padx=4)
        tk.Button(bf,text="取消",command=d.destroy,bg=self.C["border"],fg=self.C["text_1"],font=self.FN,relief="flat",padx=20,pady=4,cursor="hand2").pack(side=tk.LEFT,padx=4)

    def _cmd_run(self,cmd):
        if not cmd.strip():self._append_system("用法: /run <命令>");return
        self._append_system(f"⚡ {cmd}")
        # 沙箱：在临时目录执行
        with tempfile.TemporaryDirectory()as td:
            threading.Thread(target=lambda td=td:self.root.after(0,lambda:self._append_system(_rc(cmd,td))),daemon=True).start()

    def _cmd_search(self,q):
        if not q.strip():self._append_system("用法: /search <关键词>");return
        self._append_system(f"🔍 {q}")
        threading.Thread(target=lambda:self._do_search(q),daemon=True).start()
    def _do_search(self,q):
        try:
            u=f"https://lite.duckduckgo.com/lite/?q={urllib.parse.quote(q)}"
            rq=urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(rq,timeout=15)as r:body=r.read().decode("utf-8",errors="replace")
            body=re.sub(r'<[^>]+>',' ',body);body=re.sub(r'\s+',' ',body).strip()
            self.root.after(0,lambda:self._append_system(f"🔍 结果:\n{body[:3000]}"))
        except Exception as e:self.root.after(0,lambda:self._append_system(f"❌ {e}"))

    def _cmd_grep(self,arg):
        parts=arg.split(maxsplit=1);pat=parts[0].strip()if parts else"";sp=parts[1].strip()if len(parts)>1 else"."
        if not pat:self._append_system("用法: /grep <模式> [路径]");return
        p=_rp(sp,self.work_dir)
        if not p.exists():self._append_system(f"❌ 不存在: {p}");return
        self._append_system(f"🔎 {pat}")
        threading.Thread(target=lambda:self._do_grep(pat,p),daemon=True).start()
    def _do_grep(self,pat,sp):
        try:
            reg=re.compile(pat,re.I);rs=[];cnt=0
            if sp.is_dir():
                for f in sp.rglob("*"):
                    if cnt>=200:break
                    if f.is_file()and f.suffix in('.py','.js','.ts','.json','.yaml','.yml','.toml','.md','.txt','.html','.css','.java','.c','.cpp','.h','.rs','.go','.sh','.bat','.ps1','.xml','.cfg','.ini','.csv'):
                        try:
                            for i,l in enumerate(f.read_text(encoding="utf-8",errors="replace").splitlines(),1):
                                if reg.search(l):rs.append(f"{f}:{i}: {l.strip()[:120]}");cnt+=1
                                if cnt>=200:break
                        except:pass
            else:
                for i,l in enumerate(sp.read_text(encoding="utf-8",errors="replace").splitlines(),1):
                    if reg.search(l):rs.append(f"{i}: {l.strip()[:120]}");cnt+=1
            o="\n".join(rs)if rs else"无匹配"
            if cnt>=200:o+=f"\n... ({cnt}+条)"
            self.root.after(0,lambda:self._append_system(f"🔎 ({cnt}):\n{o[:5000]}"))
        except Exception as e:self.root.after(0,lambda:self._append_system(f"❌ {e}"))

    def _cmd_find(self,arg):
        if not arg.strip():self._append_system("用法: /find <模式> [路径]");return
        parts=arg.split(maxsplit=1);pat=parts[0];sp=parts[1]if len(parts)>1 else"."
        p=_rp(sp,self.work_dir);rs=[]
        if p.is_dir():
            for f in p.rglob(pat):
                if len(rs)<100:rs.append(str(f))
        out="\n".join(rs)if rs else"无匹配"
        if len(rs)>=100:out+="\n..."
        self._append_system(f"📁 {pat}:\n{out[:4000]}")

    def _cmd_git(self,arg):
        if not arg.strip():arg="status"
        op=arg.split()[0]
        if op not in["status","diff","log","show","blame"]:self._append_system("用法: /git status|diff|log|show|blame");return
        self._append_system(f"🔀 git {arg}")
        threading.Thread(target=lambda:self.root.after(0,lambda:self._append_system(_rc(f"git {arg}",self.work_dir,30))),daemon=True).start()

    def _cmd_py(self,code):
        if not code.strip():self._append_system("用法: /py <代码>");return
        self._append_system(f"🐍 {code[:100]}")
        threading.Thread(target=lambda:self._do_py(code),daemon=True).start()
    def _do_py(self,code):
        try:
            import io;out=io.StringIO()
            exec(code,{"__builtins__":__builtins__,"print":lambda*a,**k:print(*a,file=out,**k)})
            r=out.getvalue();self.root.after(0,lambda:self._append_system(f"🐍 输出:\n{r[:4000]}"if r else"🐍 OK"))
        except Exception as e:self.root.after(0,lambda:self._append_system(f"🐍 错误: {e}"))

    def _cmd_test(self):
        self._append_system("🧪 检测并运行测试…")
        wd=Path(self.work_dir)
        if(wd/"pyproject.toml").exists()or(wd/"setup.py").exists():self._append_system(_rc("python -m pytest --tb=short",self.work_dir,120))
        elif(wd/"Cargo.toml").exists():self._append_system(_rc("cargo test",self.work_dir,300))
        elif(wd/"package.json").exists():self._append_system(_rc("npm test",self.work_dir,300))
        else:self._append_system("未检测到已知项目类型")

    def _cmd_agent(self,task):
        if not task.strip():self._append_system("用法: /agent <任务描述>");return
        if len(self.subagents)>=MAX_SUBAGENTS:self._append_system(f"❌ 已达最大子代理数 ({MAX_SUBAGENTS})");return
        agent=SubAgent(task,self.api_key,self.api_model,self.work_dir,self._on_agent_update,self._on_agent_done)
        self.subagents[agent.id]=agent
        self._append_system(f"🤖 子代理 #{agent.id} 已启动: {task}","agent_msg")
        self._sw_p("agents")   # 切到代理面板
        # 清理已完成
        for aid in list(self.subagents.keys()):
            if self.subagents[aid].status in("done","failed","cancelled"):
                if time.time()-self.subagents[aid].start_time>60:del self.subagents[aid]

    def _cmd_agents(self):
        if not self.subagents:self._append_system("📭 无活跃子代理")
        else:
            lines=["🤖 子代理状态:"]
            for a in self.subagents.values():
                elapsed=int(time.time()-a.start_time)
                lines.append(f"  #{a.id} [{a.status}] {elapsed}s - {a.task[:60]}")
            self._append_system("\n".join(lines))

    def _cmd_compress(self):
        """手动压缩上下文"""
        self._do_compress()
        self._append_system("🗜️ 对话上下文已压缩")

    def _maybe_compress(self):
        """自动压缩：超过阈值时触发"""
        non_sys=[m for m in self.messages if m["role"]!="system"]
        if len(non_sys)>COMPRESS_THRESHOLD*2:
            self._do_compress()

    def _do_compress(self):
        """压缩对话：保留 system + 最近 8 条，中间替换为摘要"""
        non_sys=[m for m in self.messages if m["role"]!="system"]
        sys_msg=[m for m in self.messages if m["role"]=="system"]
        if len(non_sys)<=COMPRESS_THRESHOLD:return
        recent=non_sys[-8:]
        old=non_sys[:-8]
        # 生成摘要
        old_text="\n".join(f"[{m['role']}]: {m['content'][:200]}"for m in old[-20:])
        summary=f"[上下文摘要: 前{len(old)}条消息，最近讨论概要: {old_text[:500]}]"
        new_sys=sys_msg[0]["content"]+"\n"+summary if sys_msg else summary
        self.messages=[{"role":"system","content":new_sys}]+recent

    def _cmd_save(self,name):
        name=name.strip()or time.strftime("%Y%m%d_%H%M%S")
        safe=re.sub(r'[\\/:*?"<>|]','_',name);p=SESSIONS_DIR/f"{safe}.json"
        d={"name":name,"saved_at":time.strftime("%Y-%m-%d %H:%M:%S"),"messages":self.messages,"in_tok":self.in_tok,"out_tok":self.out_tok,"cost":self.cost,"turns":self.turns}
        p.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding="utf-8");self._append_system(f"💾 {p.name}")
    def _cmd_load(self,name):
        name=name.strip()
        if not name:self._cmd_list();return
        p=SESSIONS_DIR/f"{name}.json"
        if not name.endswith(".json")and not p.exists():
            for s in sorted(SESSIONS_DIR.glob("*.json"),key=lambda x:x.stat().st_mtime,reverse=True):
                if name.lower()in s.stem.lower():p=s;break
        if not p.exists():self._append_system(f"❌ 不存在: {name}");return
        try:
            d=json.loads(p.read_text(encoding="utf-8"));self.messages=d.get("messages",[]);self.in_tok=d.get("in_tok",0);self.out_tok=d.get("out_tok",0);self.cost=d.get("cost",0);self.turns=d.get("turns",0);self._update_sb()
            self.chat_text.configure(state=tk.NORMAL);self.chat_text.delete("1.0",tk.END);self.chat_text.configure(state=tk.DISABLED)
            self._append_system(f"📂 已恢复: {d.get('name',name)}")
            for m in self.messages:
                if m["role"]=="user":self._append_message("user",m["content"])
                elif m["role"]=="assistant":self._append_message("assistant",m["content"])
        except Exception as e:self._append_system(f"❌ {e}")
    def _cmd_list(self):
        ss=sorted(SESSIONS_DIR.glob("*.json"),key=lambda x:x.stat().st_mtime,reverse=True)
        if not ss:self._append_system("📭 无会话");return
        lines=["📂 会话:"]
        for i,s in enumerate(ss[:20]):
            try:d=json.loads(s.read_text(encoding="utf-8"));lines.append(f"  {i+1}. {d.get('name',s.stem)} ({len(d.get('messages',[]))}条)")
            except:lines.append(f"  {i+1}. {s.stem}")
        self._append_system("\n".join(lines))
    def _cmd_help(self):
        self._append_system(
            "📋 完整命令:\n"
            "  /read <路径> /write <路径> /run <命令> /py <代码>\n"
            "  /grep <模式> /find <名> /git <操作> /search <词>\n"
            "  /test /agent <任务> /agents /compress\n"
            "  /save /load /list /help\n\n"
            "🤖 AI 标记: [AGENT:任务] [READ:路径] [WRITE:路径]内容[/WRITE]\n"
            "  [RUN:命令] [PY:代码] [GIT:操作] [GREP:模式] [FIND:名] [SEARCH:词]\n\n"
            "⚙️ 特性:\n"
            "  · 子代理并行 (最多5个，后台执行)\n"
            "  · 上下文自动压缩 (超过阈值)\n"
            "  · 沙箱隔离 (/run 在临时目录执行)\n"
            f"  工作目录: {self.work_dir}"
        )

    # ════════════════ 聊天 ════════════════
    def _append_system(self,text,tag="system_msg"):
        self.chat_text.configure(state=tk.NORMAL)
        if self.chat_text.get("1.0","end-1c").strip():self.chat_text.insert(tk.END,"\n")
        self.chat_text.insert(tk.END,text+"\n",tag);self.chat_text.configure(state=tk.DISABLED);self.chat_text.see(tk.END)
    def _append_message(self,role,content):
        self.chat_text.configure(state=tk.NORMAL)
        label="🧑 你"if role=="user"else"🤖 DeepSeek";tl,tm=("user_label","user_msg")if role=="user"else("ai_label","ai_msg")
        if self.chat_text.get("1.0","end-1c").strip():self.chat_text.insert(tk.END,"\n")
        self.chat_text.insert(tk.END,label+"\n",tl)
        clean=self._proc_tags(content);self.chat_text.insert(tk.END,clean+"\n",tm)
        self.chat_text.configure(state=tk.DISABLED);self.chat_text.see(tk.END)
    def _stream_insert(self,text):
        clean=self._proc_tags(text)
        if clean:self.chat_text.configure(state=tk.NORMAL);self.chat_text.insert(tk.END,clean,"ai_msg");self.chat_text.configure(state=tk.DISABLED);self.chat_text.see(tk.END)

    # ── 状态栏 ──
    def _build_status_bar(self):
        bar=tk.Frame(self.root,bg=self.C["bg_status"],height=28);bar.pack(fill=tk.X,side=tk.BOTTOM);bar.pack_propagate(False)
        tk.Frame(bar,bg=self.C["border"],height=1).pack(fill=tk.X,side=tk.TOP)
        inner=tk.Frame(bar,bg=self.C["bg_status"]);inner.pack(fill=tk.BOTH,expand=True,padx=16)
        self.st_tok=tk.Label(inner,text="Tok: —",font=self.FT,fg=self.C["text_2"],bg=self.C["bg_status"]);self.st_tok.pack(side=tk.LEFT)
        tk.Label(inner,text=" │ ",font=self.FT,fg=self.C["border"],bg=self.C["bg_status"]).pack(side=tk.LEFT)
        self.st_cost=tk.Label(inner,text="¥0",font=self.FT,fg=self.C["text_2"],bg=self.C["bg_status"]);self.st_cost.pack(side=tk.LEFT)
        tk.Label(inner,text=" │ ",font=self.FT,fg=self.C["border"],bg=self.C["bg_status"]).pack(side=tk.LEFT)
        self.st_round=tk.Label(inner,text="轮0",font=self.FT,fg=self.C["text_2"],bg=self.C["bg_status"]);self.st_round.pack(side=tk.LEFT)
        self.st_mode=tk.Label(inner,text=f"{self.mode}·{self.display_model}",font=self.FT,fg=self.C["text_2"],bg=self.C["bg_status"]);self.st_mode.pack(side=tk.RIGHT)
    def _update_sb(self):
        def f(n):
            if n>=1_000_000:return f"{n/1_000_000:.1f}M"
            if n>=1_000:return f"{n/1_000:.1f}K"
            return str(n)
        self.st_tok.configure(text=f"入{f(self.in_tok)}出{f(self.out_tok)}");self.st_cost.configure(text=f"¥{self.cost:.3f}");self.st_round.configure(text=f"轮{self.turns}")

    # ── 模式/模型 ──
    def _show_mode_menu(self,e):
        m=tk.Menu(self.root,tearoff=0,bg="#2a2a3e",fg=self.C["text_1"],activebackground=self.C["accent"],activeforeground="#1e1e2e",font=self.FS)
        for o in MODE_OPTIONS:m.add_command(label=o,command=lambda o=o:self._sw_m(o))
        m.post(e.x_root,e.y_root)
    def _sw_m(self,m):self.mode=m;self.mode_label.configure(text=m);self.st_mode.configure(text=f"{self.mode}·{self.display_model}")
    def _show_model_menu(self,e):
        m=tk.Menu(self.root,tearoff=0,bg="#2a2a3e",fg=self.C["text_1"],activebackground=self.C["accent2"],activeforeground="#1e1e2e",font=self.FS)
        for o in MODEL_OPTIONS:m.add_command(label=o,command=lambda o=o:self._sw_md(o))
        m.post(e.x_root,e.y_root)
    def _sw_md(self,d):self.display_model=d;self.api_model=MODEL_MAP.get(d,"deepseek-chat");self.model_label.configure(text=d);self.st_mode.configure(text=f"{self.mode}·{self.display_model}")

    # ════════════════ 发送 ════════════════
    def _on_enter(self,e):
        if not(e.state&0x0001):self.send_message();return"break"
    def _on_shift_enter(self,e):self.input_text.insert(tk.INSERT,"\n");return"break"

    def send_message(self):
        if not self.client:messagebox.showwarning("未配置","请先设置 API Key");return
        u=self.input_text.get("1.0","end-1c").strip()
        if not u:return
        self.input_text.delete("1.0",tk.END)
        if u.startswith("/"):self._append_system(f"⚡ {u}","tool_out");self._handle_cmd(u);return
        self._maybe_compress()   # 自动压缩
        self._append_message("user",u);self.messages.append({"role":"user","content":u})
        self.send_btn.configure(state=tk.DISABLED,text="思考中…");self.status_light.configure(fg="#f9e2af");self.auto_rounds=0
        threading.Thread(target=self._stream_resp,daemon=True).start()

    def _stream_resp(self):
        try:
            stream=self.client.chat.completions.create(model=self.api_model,messages=self.messages,stream=True,stream_options={"include_usage":True},temperature=0.7,max_tokens=4096)
            full="";usage=None
            for chunk in stream:
                if chunk.usage:usage=chunk.usage
                d=chunk.choices[0].delta
                if d.content:full+=d.content;self.root.after(0,lambda t=d.content:self._stream_insert(t))
            self.messages.append({"role":"assistant","content":full})
            if usage:
                inp,out=usage.prompt_tokens or 0,usage.completion_tokens or 0
                self.in_tok+=inp;self.out_tok+=out;self.cost+=(inp/1_000_000)*PRICE_INPUT+(out/1_000_000)*PRICE_OUTPUT;self.turns+=1;self.root.after(0,self._update_sb)
            tools=self._extract_tools(full)
            if tools and self.auto_rounds<MAX_AUTO_ROUNDS:self.auto_rounds+=1;self.root.after(0,lambda:self._exec_tools(tools))
            else:self.root.after(0,self._on_resp_done)
        except Exception as e:self.root.after(0,lambda:self._stream_insert(f"\n\n[错误]{str(e)}"));self.root.after(0,self._on_resp_done)

    def _extract_tools(self,text):
        ts=[]
        for m in TAG_WRITE.finditer(text):ts.append(("WRITE",m.group(1).strip(),m.group(2).strip()))
        for m in TAG_READ.finditer(text):ts.append(("READ",m.group(1).strip()))
        for m in TAG_RUN.finditer(text):ts.append(("RUN",m.group(1).strip()))
        for m in TAG_PY.finditer(text):ts.append(("PY",m.group(1).strip()))
        for m in TAG_GIT.finditer(text):ts.append(("GIT",m.group(1).strip()))
        for m in TAG_GREP.finditer(text):ts.append(("GREP",m.group(1).strip()))
        for m in TAG_FIND.finditer(text):ts.append(("FIND",m.group(1).strip()))
        for m in TAG_SEARCH.finditer(text):ts.append(("SEARCH",m.group(1).strip()))
        for m in TAG_AGENT.finditer(text):ts.append(("AGENT",m.group(1).strip()))
        return ts

    def _exec_tools(self,tools):
        rs=[]
        for t in tools:
            if t[0]=="READ":rs.append(self._tool_read(t[1]))
            elif t[0]=="RUN":rs.append(self._tool_run(t[1]))
            elif t[0]=="PY":rs.append(self._tool_py(t[1]))
            elif t[0]=="GIT":rs.append(self._tool_git(t[1]))
            elif t[0]=="GREP":rs.append(self._tool_grep(t[1]))
            elif t[0]=="FIND":rs.append(self._tool_find(t[1]))
            elif t[0]=="SEARCH":rs.append(self._tool_search(t[1]))
            elif t[0]=="WRITE":rs.append(self._tool_write(t[1],t[2]))
            elif t[0]=="AGENT":rs.append(self._tool_agent(t[1]))
        combined="\n".join(rs)
        if combined:self._append_system(f"🔧 工具:\n{combined[:5000]}","tool_out");self.messages.append({"role":"user","content":f"[工具结果]\n{combined}"})
        if self.auto_rounds<MAX_AUTO_ROUNDS:threading.Thread(target=self._stream_resp,daemon=True).start()
        else:self.root.after(0,self._on_resp_done)

    def _tool_agent(self,task):
        if len(self.subagents)>=MAX_SUBAGENTS:return f"AGENT: 已达上限({MAX_SUBAGENTS})"
        agent=SubAgent(task,self.api_key,self.api_model,self.work_dir,self._on_agent_update,self._on_agent_done)
        self.subagents[agent.id]=agent
        return f"子代理 #{agent.id} 已创建: {task}"

    def _tool_read(self,path):
        p=_rp(path,self.work_dir)
        try:
            if not p.exists():return f"READ: 不存在 {p}"
            if p.is_dir():
                es=sorted(p.iterdir(),key=lambda x:(not x.is_dir(),x.name.lower()))[:50]
                return f"目录 {p}:\n"+"\n".join(f"  {'📁'if e.is_dir()else'📄'} {e.name}"for e in es)
            if p.stat().st_size>MAX_FILE_KB*1024:return"READ: 过大"
            return f"文件 {p.name}:\n{p.read_text(encoding='utf-8',errors='replace')[:6000]}"
        except Exception as e:return f"READ 错误: {e}"
    def _tool_write(self,path,content):
        p=_rp(path,self.work_dir)
        try:p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content,encoding="utf-8");return f"已写入 {p}"
        except Exception as e:return f"WRITE 错误: {e}"
    def _tool_run(self,cmd):
        with tempfile.TemporaryDirectory()as td:return f"RUN ({cmd}):\n{_rc(cmd,td)}"
    def _tool_py(self,code):
        try:import io;out=io.StringIO();exec(code,{"__builtins__":__builtins__,"print":lambda*a,**k:print(*a,file=out,**k)});r=out.getvalue();return f"PY 输出:\n{r[:3000]}"if r else"PY OK"
        except Exception as e:return f"PY 错误: {e}"
    def _tool_git(self,op):return f"GIT {op}:\n{_rc(f'git {op}',self.work_dir,30)}"
    def _tool_grep(self,arg):
        parts=arg.split(maxsplit=1);pat=parts[0];sp=parts[1]if len(parts)>1 else".";p=_rp(sp,self.work_dir)
        try:
            reg=re.compile(pat,re.I);rs=[];cnt=0
            if p.is_dir():
                for f in p.rglob("*"):
                    if cnt>=50:break
                    if f.is_file()and f.suffix in('.py','.js','.ts','.json','.yaml','.yml','.toml','.md','.txt','.html','.css','.java','.c','.cpp','.h','.rs','.go','.sh','.bat','.ps1','.xml','.cfg','.ini','.csv'):
                        try:
                            for i,l in enumerate(f.read_text(encoding="utf-8",errors="replace").splitlines(),1):
                                if reg.search(l):rs.append(f"{f}:{i}:{l.strip()[:120]}");cnt+=1
                                if cnt>=50:break
                        except:pass
            else:
                for i,l in enumerate(p.read_text(encoding="utf-8",errors="replace").splitlines(),1):
                    if reg.search(l):rs.append(f"{i}:{l.strip()[:120]}");cnt+=1
            return f"GREP /{pat}/ ({cnt}):\n"+"\n".join(rs)if rs else f"GREP: 无匹配"
        except Exception as e:return f"GREP 错误: {e}"
    def _tool_find(self,pat):
        p=_rp(pat,self.work_dir);rs=[]
        if Path(self.work_dir).is_dir():
            for f in Path(self.work_dir).rglob(pat):
                if len(rs)<30:rs.append(str(f))
        return f"FIND {pat}:\n"+"\n".join(rs)if rs else f"FIND: 无匹配"
    def _tool_search(self,q):
        try:
            u=f"https://lite.duckduckgo.com/lite/?q={urllib.parse.quote(q)}"
            rq=urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(rq,timeout=15)as r:body=r.read().decode("utf-8",errors="replace")
            body=re.sub(r'<[^>]+>',' ',body);body=re.sub(r'\s+',' ',body).strip()
            return f"SEARCH {q}:\n{body[:2000]}"
        except Exception as e:return f"SEARCH 错误: {e}"

    def _on_resp_done(self):
        self.send_btn.configure(state=tk.NORMAL,text="发送");self.status_light.configure(fg="#a6e3a1");self.input_text.focus()
        if len(self.messages)>41:self.messages=[self.messages[0]]+self.messages[-40:]
    def on_close(self):_save_tf(self.app_tasks);self.root.destroy()
    def run(self):self.root.mainloop()

def main():DeepSeekChatApp().run()
if __name__=="__main__":main()
