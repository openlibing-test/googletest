#!/usr/bin/env python3
"""clang-tidy wrapper：转发用户 args，实时 tee stdout+stderr，生成 SARIF

设计目标（设计文档 §2.2.2 / §3.4）：
  1. 接收 pre-commit 传来的 args，区分用户参数（转发给 clang-tidy）和脚本参数（用 --_ 前缀）
  2. 实时 tee clang-tidy 的 stdout+stderr：同时输出到 CI 日志 + pipe 给 clang-tidy-sarif 生成 SARIF
  3. 失败语义区分两种运行模式：
     - pre-commit 集成（PRE_COMMIT=1）：检测诊断行（warning/error），有诊断就返回非零退出码，
       不依赖 clang-tidy 退出码（.clang-tidy 的 WarningsAsErrors 默认为空，warning 不影响退出码）
     - 独立运行（CI workflow / 手动）：用 clang-tidy 原始退出码，行为不变
  4. 编译数据库去重：按 file 保留首条记录，另存 <cdb 目录>/cdb-deduped/ 作为 -p 目录，
     避免多 target 共用源文件时同一文件被重复扫描（见 prepare_build_dir）
  5. SARIF 路径相对化由 pre-commit-action / upload-sarif-action 插件内置处理，本脚本不做

关键细节：clang-tidy 的输出分流
  - stdout: 实际诊断行（filename:line:col: warning: ...）
  - stderr: 进度信息（[N/M] Processing file...）、警告数量汇总、统计信息
  两路都需 pipe 给 clang-tidy-sarif 才能生成完整 SARIF（等价 bash 版本的 `2>&1` 合并）。

用法（pre-commit local hook）：
    entry: python3 scripts/run-clang-tidy.py
    args:
      - --config-file=.clang-tidy    # 用户参数，转发给 clang-tidy
      - --quiet                       # 用户参数
      - --_sarif-output=/tmp/pre-commit-reports/clang-tidy.sarif  # 脚本参数

环境变量：
    CDB                         编译数据库路径，默认 build/compile_commands.json
    PRE_COMMIT_REPORT_DIR       SARIF 输出目录，默认 /tmp/pre-commit-reports
    PRE_COMMIT                  pre-commit 框架运行 hook 时自动设置为 1，用于区分运行模式
"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading


# clang-tidy 诊断行正则：/path/to/file.c:10:15: warning: ... 或 error: ...
# pre-commit 模式下用此正则检测输出中是否有诊断行，决定退出码
DIAGNOSTIC_PATTERN = re.compile(r'^.*:\d+:\d+:\s+(warning|error):')


def parse_args():
    """解析脚本参数 + 收集用户传给 clang-tidy 的参数。

    使用 --_ 前缀区分脚本自己的参数和用户参数，避免冲突。
    allow_abbrev=False 防止 --q 被 --quiet 缩写匹配。
    """
    parser = argparse.ArgumentParser(
        add_help=False,
        allow_abbrev=False,
        description="clang-tidy wrapper",
    )
    # 脚本自己的参数
    parser.add_argument(
        "--_sarif-output",
        default=os.path.join(
            os.environ.get("PRE_COMMIT_REPORT_DIR", "/tmp/pre-commit-reports"),
            "clang-tidy.sarif",
        ),
        help="SARIF 输出路径（脚本参数）",
    )
    parser.add_argument(
        "--_cdb",
        default=os.environ.get("CDB", "build/compile_commands.json"),
        help="编译数据库路径（脚本参数）",
    )
    parser.add_argument(
        "--_clang-tidy-sarif",
        default=".gitcode/bin/clang-tidy-sarif",
        help="clang-tidy-sarif 二进制路径（脚本参数）",
    )
    parser.add_argument(
        "--_no-sarif",
        action="store_true",
        help="只跑 clang-tidy 不生成 SARIF（本地拦截模式）",
    )
    # parse_known_args 返回 (script_args_namespace, user_args_list)
    # - script_args: 脚本自己的参数（--_ 前缀，含 _cdb / _sarif_output 等）
    # - user_args:   用户传给 clang-tidy 的参数（未识别的 args，原样转发给 clang-tidy）
    script_args, user_args = parser.parse_known_args()
    return script_args, user_args


def _write_line(stream, text):
    """向目标流写一行并 flush，流不可写时忽略（tee 线程收尾阶段的 BrokenPipe 等）。"""
    try:
        stream.write(text)
        stream.flush()
    except (IOError, OSError, ValueError):
        pass


def load_cdb(cdb_path):
    """读取 compile_commands.json（缺失则 fail-fast 并提示先跑 cmake）。"""
    if not os.path.isfile(cdb_path):
        print(
            f"ERROR: compile_commands.json not found at {cdb_path}",
            file=sys.stderr,
        )
        print(
            "Hint: run 'cmake -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON' first",
            file=sys.stderr,
        )
        sys.exit(1)
    with open(cdb_path, "r", encoding="utf-8") as f:
        return json.load(f)


def unique_entries(entries):
    """按 file 去重编译数据库记录（保留首条），据此提取全量扫描的源文件。

    实现全量扫描硬目标：从编译数据库提取所有源文件，而非硬编码 src/*.c。
    同一文件被多个 target 编译时只保留首条，命令行传参随之去重——只去重传参是无效的，
    clang-tidy 会拿文件回数据库查全部编译命令行。
    """
    unique, seen = [], set()
    for entry in entries:
        file_path = entry.get("file")
        if not file_path or file_path in seen:
            continue
        seen.add(file_path)
        unique.append(entry)
    return unique


def prepare_build_dir(cdb_path, entries, unique):
    """编译数据库去重：去重后的记录另存 <cdb 目录>/cdb-deduped/，返回 clang-tidy 的 -p 目录。

    为什么必须做在数据库层面：clang-tidy 拿到文件后会回编译数据库查该文件的**全部**
    编译命令行，并对每条命令行各跑一次分析（CompilationDatabase::getCompileCommands 语义）。
    CMake 同时建 SHARED + STATIC 且共用源列表时（多 target 共用源码属常态），同一文件会被
    重复扫描，导致耗时翻倍、同一问题在平台重复入库。
    原始 compile_commands.json 不覆盖，保持可追溯；无重复记录时不额外生成目录。
    """
    build_dir = os.path.dirname(cdb_path) or "."
    if len(unique) == len(entries):
        return build_dir
    dedup_dir = os.path.join(build_dir, "cdb-deduped")
    os.makedirs(dedup_dir, exist_ok=True)
    dedup_path = os.path.join(dedup_dir, "compile_commands.json")
    with open(dedup_path, "w", encoding="utf-8") as f:
        json.dump(unique, f, indent=2)
    print(
        f"[cdb] {len(entries)} entries -> {len(unique)} unique files, "
        f"deduped db: {dedup_path}",
        flush=True,
    )
    return dedup_dir


def is_pre_commit_mode():
    """检测是否在 pre-commit 集成环境中运行。

    pre-commit 框架运行 hook 时自动设置 PRE_COMMIT=1 环境变量，
    直接命令行或 CI workflow 运行脚本时该变量不存在。
    """
    return os.environ.get("PRE_COMMIT") == "1"


def run_clang_tidy_no_sarif(cmd_clang_tidy, pre_commit_mode):
    """本地拦截模式：只跑 clang-tidy，不生成 SARIF。

    pre-commit 模式下 pipe stdout/stderr 逐行检测诊断行，有诊断返回非零退出码；
    非 pre-commit 模式直接输出到 sys.stdout/sys.stderr，用 clang-tidy 原始退出码。
    """
    if not pre_commit_mode:
        p1 = subprocess.Popen(
            cmd_clang_tidy,
            stdout=sys.stdout,
            stderr=sys.stderr,
            text=True,
            encoding="utf-8",
        )
        p1.wait()
        return p1.returncode

    has_diagnostics = [False]

    def tee_and_detect(stream, sink_console):
        """逐行读取 clang-tidy 输出，写到 console 并检测诊断行。"""
        while True:
            line = stream.readline()
            if not line:
                break
            _write_line(sink_console, line)
            if DIAGNOSTIC_PATTERN.search(line):
                has_diagnostics[0] = True

    p1 = subprocess.Popen(
        cmd_clang_tidy,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    t_stdout = threading.Thread(target=tee_and_detect, args=(p1.stdout, sys.stdout))
    t_stderr = threading.Thread(target=tee_and_detect, args=(p1.stderr, sys.stderr))
    t_stdout.start()
    t_stderr.start()
    p1.wait()
    t_stdout.join()
    t_stderr.join()
    return 1 if has_diagnostics[0] else p1.returncode


def run_clang_tidy_with_sarif(cmd_clang_tidy, cmd_sarif, sarif_output, pre_commit_mode):
    """SARIF 模式：实时 tee stdout+stderr 到 CI 日志 + pipe 给 clang-tidy-sarif。

    关键注意：clang-tidy 的实际诊断行（filename:line:col: warning: ...）输出到 stdout，
    而进度信息、警告数量统计等输出到 stderr。两路都需 pipe 给 clang-tidy-sarif，
    否则 SARIF 的 results 为空。用两个 tee 线程分别处理 stdout 和 stderr。

    pre-commit 模式下逐行检测诊断行，有诊断返回非零退出码；
    非 pre-commit 模式用 clang-tidy 原始退出码。
    """
    sarif_dir = os.path.dirname(sarif_output)
    if sarif_dir:
        os.makedirs(sarif_dir, exist_ok=True)
    has_diagnostics = [False]

    def tee(stream, sink_console, sink_sarif):
        """逐行读取 clang-tidy 输出，同时写到 CI 日志和 clang-tidy-sarif 的 stdin。

        pre-commit 模式下额外检测诊断行决定退出码。
        """
        while True:
            line = stream.readline()
            if not line:
                break
            _write_line(sink_console, line)
            _write_line(sink_sarif, line)
            if pre_commit_mode and DIAGNOSTIC_PATTERN.search(line):
                has_diagnostics[0] = True

    # with 管理 SARIF 文件句柄：异常路径下也会关闭，避免句柄泄漏（P2-12）
    with open(sarif_output, "w", encoding="utf-8") as sarif_file:
        # 启动 clang-tidy-sarif，stdin 接收 tee 后的 stdout+stderr 合并流
        p2 = subprocess.Popen(
            cmd_sarif,
            stdin=subprocess.PIPE,
            stdout=sarif_file,
            text=True,
            encoding="utf-8",
        )
        # 启动 clang-tidy，stdout 和 stderr 都要捕获（诊断行在 stdout）
        p1 = subprocess.Popen(
            cmd_clang_tidy,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        # 两个线程分别处理 stdout 和 stderr，都写入 clang-tidy-sarif 的 stdin
        t_stdout = threading.Thread(target=tee, args=(p1.stdout, sys.stdout, p2.stdin))
        t_stderr = threading.Thread(target=tee, args=(p1.stderr, sys.stderr, p2.stdin))
        t_stdout.start()
        t_stderr.start()

        p1.wait()
        t_stdout.join()
        t_stderr.join()
        p2.stdin.close()  # 关闭 stdin 让 clang-tidy-sarif 处理完
        p2.wait()
    if pre_commit_mode and has_diagnostics[0]:
        return 1
    return p1.returncode


def main():
    script_args, user_args = parse_args()
    pre_commit_mode = is_pre_commit_mode()
    # 从编译数据库提取全量扫描源文件，并做数据库层去重（详见 unique_entries / prepare_build_dir）
    entries = load_cdb(script_args._cdb)
    unique = unique_entries(entries)
    files = [entry["file"] for entry in unique]
    build_dir = prepare_build_dir(script_args._cdb, entries, unique)
    # 构造 clang-tidy 命令：clang-tidy -p <去重后的 cdb 目录> <user_args> <files>
    cmd_clang_tidy = ["clang-tidy", "-p", build_dir] + user_args + files

    if script_args._no_sarif:
        exit_code = run_clang_tidy_no_sarif(cmd_clang_tidy, pre_commit_mode)
    else:
        # fail-fast：SARIF 二进制随仓预置（执行机无外网，禁止运行时下载）
        if not os.path.isfile(script_args._clang_tidy_sarif):
            print(
                f"ERROR: clang-tidy-sarif not found at {script_args._clang_tidy_sarif}",
                file=sys.stderr,
            )
            print(
                "Hint: copy the prebuilt binary from clang-tidy-full-scan-demo "
                ".gitcode/bin/ or pass --_clang-tidy-sarif",
                file=sys.stderr,
            )
            return 1
        # 确保二进制可执行（部分解包方式会丢失可执行位）
        try:
            os.chmod(script_args._clang_tidy_sarif, 0o755)
        except OSError as exc:
            print(
                f"WARN: chmod {script_args._clang_tidy_sarif} failed: {exc}",
                file=sys.stderr,
            )
        exit_code = run_clang_tidy_with_sarif(
            cmd_clang_tidy,
            [script_args._clang_tidy_sarif],
            script_args._sarif_output,
            pre_commit_mode,
        )
        print(
            f"clang-tidy hook finished, SARIF: {script_args._sarif_output}",
            flush=True,
        )

    # exit_code 已由上层函数根据 pre_commit_mode 决定：
    # - pre-commit 集成：有诊断行返回 1，否则用 clang-tidy 退出码
    # - 独立运行：用 clang-tidy 原始退出码
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
