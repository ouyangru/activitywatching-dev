# ACM Practice

一个独立于 ActivityWatching 主站的本地 C++11 ACM 训练页，用于在浏览器里写代码、运行样例、自定义输入和提交隐藏测试。

## 启动

确保本机 `g++` 可用：

```bash
g++ --version
python3 acm_practice/server.py
```

然后打开：

```text
http://127.0.0.1:8765
```

Windows 如果使用 WSL2，通常可以直接从 Windows 浏览器访问这个 localhost 地址。

## 功能

- C++11：`g++ -std=c++11 -O2`
- 代码自动保存在浏览器 `localStorage`
- 运行公开样例
- 自定义 stdin
- 隐藏测试提交判题
- AC / WA / CE / RE / TLE
- 编译和执行耗时显示
- `Ctrl + Enter` 运行样例
- `Ctrl + Shift + Enter` 提交

题库在 `acm_practice/problems.json`，可以直接继续追加题目和隐藏测试。

## 安全说明

这个服务会编译并执行你提交的 C++ 程序，因此默认只监听 `127.0.0.1`。不要把它直接暴露到公网。资源限制主要用于防止练习代码意外死循环、爆内存或疯狂输出，不是完整的恶意代码沙箱。

如果确实需要在可信局域网访问，可以显式指定：

```bash
python3 acm_practice/server.py --host 0.0.0.0 --port 8765
```

此时只应在可信网络环境中使用。
