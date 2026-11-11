# `.github/bin/` 预置二进制目录

本目录存放随仓库提交的预置二进制工具，避免 CI 执行机访问外网下载（设计文档 §7.3）。

## 当前预置工具

| 文件 | 用途 | 来源 | 版本 | License |
| --- | --- | --- | --- | --- |
| `clang-tidy-sarif` | 将 clang-tidy 的 stderr 文本输出转换为 SARIF v2.1.0 | [sarif-rs 官方 GitHub Release](https://github.com/psastras/sarif-rs/releases) `clang-tidy-sarif-v0.8.0/x86_64-unknown-linux-gnu` | 0.8.0 | MIT（保留于 `clang-tidy-sarif-LICENSE`） |
| `clang-tidy-sarif-LICENSE` | MIT License 全文，Copyright (c) 2021 Paul Sastrasinh | 同上 | — | — |

## 工作原理

clang-tidy-sarif 从 stdin 读取 **clang-tidy 的 stdout+stderr 合并文本输出**（不是 `--export-fixes` 的 YAML），转换后输出 SARIF v2.1.0 到 stdout。典型用法：

```bash
clang-tidy -p build src/*.c 2>&1 | .github/bin/clang-tidy-sarif > sarif/clang-tidy.sarif
```

> clang-tidy 的实际诊断行输出到 stdout，进度信息和警告数量统计输出到 stderr，故需用 `2>&1` 合并后再 pipe。

## 预置二进制来源说明（License 合规）

clang-tidy-sarif 上游 License 为 **MIT**（[Cargo.toml](https://github.com/psastras/sarif-rs/blob/main/clang-tidy-sarif/Cargo.toml)），允许任何用途包括 redistribute、sublicense、merge、publish、distribute。本仓预置该二进制的方式符合 MIT 条款，仅需保留 LICENSE 文件，已通过 `clang-tidy-sarif-LICENSE` 满足。

预置二进制的下载方式（开发机执行）：

```bash
# 1. 下载 Linux x64 预编译二进制
curl -sSL -o <repo>/.github/bin/clang-tidy-sarif \
  https://github.com/psastras/sarif-rs/releases/download/clang-tidy-sarif-v0.8.0/clang-tidy-sarif-x86_64-unknown-linux-gnu

# 2. 下载 LICENSE
curl -sSL -o <repo>/.github/bin/clang-tidy-sarif-LICENSE \
  https://raw.githubusercontent.com/psastras/sarif-rs/main/LICENSE

# 3. 标记 git 文件模式为可执行（关键，否则 Linux runner Permission denied）
chmod +x <repo>/.github/bin/clang-tidy-sarif
git update-index --chmod=+x .github/bin/clang-tidy-sarif
git ls-files -s .github/bin/clang-tidy-sarif   # 期望 100755
```

## 升级到新版本

如需升级到新版本（如 0.9.0+）：

```bash
# 替换二进制（注意版本号变化）
curl -sSL -o <repo>/.github/bin/clang-tidy-sarif \
  https://github.com/psastras/sarif-rs/releases/download/clang-tidy-sarif-v0.9.0/clang-tidy-sarif-x86_64-unknown-linux-gnu
chmod +x <repo>/.github/bin/clang-tidy-sarif
git update-index --chmod=+x .github/bin/clang-tidy-sarif

# LICENSE 通常不变，如上游更新则一并替换
```

升级后建议在测试仓触发一次 `clang-tidy-scan` workflow，验证 SARIF 输出格式仍能被后端 `StandardSarifParser` 解析。

## 文件模式自检

CI workflow 中已加 `chmod +x .github/bin/clang-tidy-sarif` 双保险（设计文档 §7.4），
首次提交二进制到仓库时已用 `git update-index --chmod=+x` 标记，clone 后直接可执行。
