# RepeatFS Bug 报告

**范围**：`repeatfs/` 主包下所有源代码（不包括 `plugins/` 子目录及 `build/` 打包产物）。
**代码版本**：`dev.2025.07.01.distributed` 分支，`Core.VERSION = 0.14.1`。
**报告日期**：2026-08-01。

---

## 一、严重 / 导致崩溃的 Bug

### 1.1 `routing.py:15-25` — `mount` / `unmount` 引用未定义变量

```python
def mount(self):
    for plugin in self.core.plugins:
        plugin.syscalls[func](arg1)   # NameError
    return func(arg1)                # NameError

def unmount(self):
    for plugin in self.core.plugins:
        plugin.syscalls[func](arg1)
    return func(arg1)
```

`func` 与 `arg1` 是不存在的局部变量。一旦有人调用 `Routing.mount()` 或 `Routing.unmount()`，会立即抛 `NameError`。`Core.unmount` 直接被 `Fuse.destroy` 调用，绕过了 `Routing.unmount`，所以目前没炸；但 `Routing.mount` 也没有任何调用方 —— 看起来是一段没写完的代码，应直接删掉或补齐按系统调用分发。

---

### 1.2 `core.py:485` — O_DIRECT 检查的运算符优先级错误

```python
if file_entry.derived_source or file_entry.api or (self.direct_support and (info.flags & os.O_DIRECT > 0)):
    info.direct_io = self.direct_support
    info.keep_cache = False
```

`>` 的优先级高于 `&`，因此实际被解释为：

```python
info.flags & (os.O_DIRECT > 0)
# == info.flags & False
# == 0
```

O_DIRECT 分支永远不会触发，等于把这段逻辑写死了。修正：

```python
(self.direct_support and ((info.flags & os.O_DIRECT) > 0))
```

---

### 1.3 `core.py:593` — `os.truncate` 被传了文件描述符

```python
else:
    err = os.truncate(desc_entry.fs_descriptor, length)
```

`os.truncate(path, length)` 第一个参数是路径，fd 需要用 `os.ftruncate(fd, length)`。当前调用等价于对 `os.truncate(<int>, length)`，在 Linux 上 `int` 不可被解析为路径，会抛 `TypeError` 或返回 `FileNotFoundError`。

修正：

```python
err = os.ftruncate(desc_entry.fs_descriptor, length)
```

---

### 1.4 `core.py:521-526` — 异常分支下 `read_buf` 未定义

```python
with desc_entry.fs_lock:
    try:
        os.lseek(desc_entry.fs_descriptor, offset, os.SEEK_SET)
        read_buf = os.read(desc_entry.fs_descriptor, length)
    except Exception as e:
        self.log("Read error: {}".format(e), self.core.LOG_DEBUG)

return read_buf
```

如果 `os.lseek` 在 `read_buf` 赋值前抛异常，`read_buf` 仍是未绑定局部变量，函数会抛 `UnboundLocalError`，并导致 FUSE 调用栈一路向上失败。`write()`（第 553 行附近）的 `write_len` 同样有此问题。

修正方法：进入 `try` 之前初始化 `read_buf = b""` / `write_len = 0`。

---

### 1.5 `fuse.py:105` — 格式字符串里 `{3},{4}` 漏了空格

```python
self.core.log("IO: write ({0}, {1}, {2}, {3},{4})".format(
    path, offset, info.fh, info.writepage, len(buf)), self.core.LOG_IO)
```

占位符和参数数量本身是匹配的（5 个对 5 个），但 `{3},{4}` 中间没有空格，输出时 `info.writepage` 和 `len(buf)` 会粘在一起。属于明显的笔误。

---

### 1.6 `provenance/graph_old.py` — 缺少类头，整个文件无法 import

```
import ast
import pygraphviz

    def _add_file(self, file_index, graph, info, target):
        ...
```

`_add_file` / `_add_process` / `_add_io` / `_build_graph_old` / `_group_process` / `_collapse_processes` / `_collapse_files` / `_wrap_graph_head` / `_wrap_graph_tail` / `virt_graph_svg` / `repeat_test` 等都是顶层 `def`，缩进还被放在 `import` 块之下。没有外层 `class` / `def`，Python 解释器在 import 阶段会直接抛 `IndentationError`。

如果这份代码已经废弃，应从包内移除；如果还在用，需要补上 `class GraphOld:` 之类的类声明并去缩进。

---

### 1.7 `provenance/process_record.py:430` — `derived_soruce` 拼写错误

```python
def repeat_test(self, process, handle):
    file_path = process.cache_entry.file_entry.derived_soruce.paths["abs_real"]
```

正确名字是 `derived_source`。一旦 `repeat_test` 被实际调用，会抛 `AttributeError`。

---

## 二、逻辑 / 正确性 Bug

### 2.1 `core.py:127-128` — `virt_mtime` 比较顺序不对

```python
if entry.virt_mtime != cache_entry.file_entry.virt_mtime:
    cache_entry.io(CacheEntry.IO_RESET, 0, None, 1, desc_entry.id)
```

新构建的 `entry.virt_mtime` 与 *已缓存* 的 `cache_entry.file_entry.virt_mtime` 比较。同一进程多次 open 时后者可能没及时更新，导致不必要的 IO_RESET 或反过来漏 reset。建议改成基于文件 mtime + 路径做 hash 比较，或显式在每次 reset 时同步两个 `file_entry`。

---

### 2.2 `core.py:147` — 假设缓存项一定存在

```python
elif desc_entry.file_entry.derived_source:
    path = desc_entry.file_entry.paths["abs_real"]
    CacheEntry.entries[path].unregister_descriptor(desc_entry.id)
```

`create_descriptor` 会在 derived 分支注册 `CacheEntry`，但 `flags` 为 `None` 或临时描述符的场景下跳过注册；`remove_descriptor` 仍然走这条 `elif` 会 `KeyError`。需要兜底（如 `CacheEntry.entries.get(path)`）或保证创建路径注册。

---

### 2.3 `core.py:522, 553` — `os.read` / `os.write` 的短读 / 短写未处理

```python
read_buf = os.read(desc_entry.fs_descriptor, length)
```

`os.read` 在文件结尾或管道上可能返回少于 `length` 字节。FUSE 把返回值当作当次完整响应处理，下一次会用错位的 offset 重读同一段数据。同样问题在 `os.write`，短写被当作错误长度返回。建议循环直到读够 `length` 字节或 EOF。

---

### 2.4 `core.py:461` — 创建文件后立刻关闭

```python
if create:
    os.close(os.open(file_entry.paths["abs_real"], os.O_WRONLY | os.O_CREAT, mode))
    file_entry = FileEntry(path, self)
    info.flags = os.O_RDWR
```

`O_CREAT` 创建后立刻 `os.close()` 释放 fd：

1. 与并发 create 存在 TOCTOU；
2. 如果后续 `FileEntry` 构造失败，会留下一个 0 字节空文件。

建议保留 fd 走 `create_descriptor` 流程，或在 finally 中确保失败时清理。

---

### 2.5 `core.py:581-583` — API 分支 `pass` 不返回

```python
if desc_entry.file_entry.api:
    # Truncation has no effect on API
    pass

elif desc_entry.file_entry.derived_source:
    ...
```

不会出错（后面会继续走 cleanup），但语义上 `err` 永远不会被显式返回。应 `return 0` 或 `return err`。

---

### 2.6 `file_entry.py:158` — action 元组的 2-tuple / 3-tuple 混用

```python
derived_action = derived_source.derived_actions[virt_base][:2]
self.init_size = self.core.configuration.actions[derived_action]["init_size"]
```

`derived_actions` 的 value 在 `_populate_actions` 里是 `action + (match.groups(),)`（3-tuple），`[:2]` 取前两个刚好对得上 `actions` 字典的 `(match, ext)` key；但 `virt_action` 保存的是 3-tuple（含 match groups），这里又临时切回 2-tuple。两套口径混用，建议统一为同一结构并在 `_populate_actions` 时就生成 `match_groups`。

---

### 2.7 `file_entry.py:77` — inline_cmd 用空串拼接

```python
inline_fields = virt_path.split(inline_sep)
self.inline_cmd = "".join(inline_fields[1:])
```

`"".join(...)` 会把所有字段挤在一起，丢失原有分隔符。如果 separator 是有意义的（例如 `++`），应改 `"++".join(...)` 或保留原 `split` 出来的奇偶位结构。

---

### 2.8 `cache_entry.py:256` — `io()` 返回值在调用方用法不一致

```python
return bytes(ret_data) if operation == self.IO_READ else ret_size
```

- `IO_WRITE` 返回 `ret_size`（已写入字节数），`core.py:546` 当作 `direct_size` 使用 —— OK；
- `IO_TRUNCATE` 返回 `ret_size`，`core.py:590` 没用到返回值 —— 浪费但无害；
- `IO_RESET` 返回 `size`（请求大小），`core.py:128` 也没用 —— 浪费但无害。

主要是文档 / 一致性问题。

---

### 2.9 `cache_entry.py:393` — `end` 标志算了却不用

```python
end = (cache_handle.tell() == file_size)
return (block_data, end)
```

`get_disk_block` 调用方只取 `[0]`，`end` 是死字段。要么删掉，要么让上层真正使用它来做 EOF 判断。

---

### 2.10 `cache_entry.py:386-395` — 文件大小在 open 前快照，并发场景可能撕裂

`file_size = os.path.getsize(self.cache_path)` 在 `with open(...)` 之前一次性读取，并发写入下可能拿到与读取不一致的快照，读出撕裂的 block。

---

### 2.11 `process_io.py:48, 66` — `BufferedReader` 分支的 finally 不必要但无害

```python
try:
    if isinstance(self.stream_buffer, io.BufferedReader):
        ret_data = self.stream_buffer.read(size)
        return ret_data
    ...
finally:
    self.lock.notifyAll()
```

`BufferedReader` 路径没等待却也 `notifyAll`，功能上不影响（没人会等这个 notify），但语义混乱。

---

### 2.12 `process_io.py:262` — `write()` 返回值的双重含义

```python
if not self.context_owner(descriptor=descriptor): return len(data)
```

对非 owner，调用方 `core.py:544-546` 把它当作"需要写回内存缓存的字节数"。当前恰好能对上，但返回值的"长度"和"未走 stream 的字节数"两套语义混在一起，未来易踩坑。建议返回单独语义（如 0 表示"全部交给 mem cache"，负值/异常表示其它）。

---

### 2.13 `api.py:84` — `write()` 在 pipe 已关闭时仍返回 `len(buf)`

`core.py:537` 的 `write_len` 永远被当作"成功写入长度"，调用方无法察觉写入失败。属于风格问题。

---

### 2.14 `api.py:51-58` — 行读取器没有 EOF 处理

```python
while True:
    char = os.read(fd, 1)
    if char == b"\n": break
    line += char
```

读到 EOF（`char == b""`）时不会 break，会无限循环。应：

```python
if not char:
    raise EOFError("API pipe closed mid-line")
if char == b"\n": break
```

---

### 2.15 `api.py:104-110` — `read()` 未收到命令时可能永久阻塞

```python
def read(self, length):
    if self.state == self.STATE_START:
        return ""
    return os.read(self.output_r, length)
```

`STATE_START` 时返回 `""`，FUSE 会再次调用本函数。`core.py:512` 紧接着调用 `os.read(self.output_r, length)`，如果对端尚未产生数据（pipe 仍开着），可能阻塞。

建议在 `read()` 内直接 `os.read` 处理非阻塞/阻塞语义，或由调用方根据 `state` 决策。

---

### 2.16 `descriptor_entry.py:72-73` — 直接 `os.open` 没有 `O_CLOEXEC` / 失败回滚

```python
if not file_entry.derived_source and not file_entry.api and flags is not None:
    self.fs_descriptor = os.open(file_entry.paths["abs_real"], flags)
```

如果 `os.open` 抛异常（例如权限），已经构造好的对象留在 `_desc_lookup` / `_file_lookup` 中；下次 `remove_descriptor` 又会用到。需要 `try/except` 中清理注册项。

另：缺少 `O_CLOEXEC`，子进程会继承 fd（衍生自 `repeatfs` 时一般无害但不符合 FUSE daemon 习惯）。

---

### 2.17 `descriptor_entry.py:97-99` — 双重查找的 TOCTOU

```python
DescriptorEntry._file_lookup[self.file_entry.paths["abs_real"]].remove(self.id)
if not DescriptorEntry._file_lookup[self.file_entry.paths["abs_real"]]:
    del DescriptorEntry._file_lookup[self.file_entry.paths["abs_real"]]
```

两次查找同一 key 之间，可能被其他线程/进程改掉。建议：

```python
path = self.file_entry.paths["abs_real"]
with DescriptorEntry._lock:
    s = DescriptorEntry._file_lookup.get(path)
    if s:
        s.discard(self.id)
        if not s:
            del DescriptorEntry._file_lookup[path]
```

---

### 2.18 `descriptor_entry.py:32-44` — `gen_pipe` 不更新 `_file_lookup`

pipe 描述符只写入 `_pipe_lookup`，没有出现在 `_file_lookup`。`rename` 和 `remove` 都基于 `_file_lookup` 走更新/清理逻辑 —— pipe 会泄漏。

---

### 2.19 `configuration.py:124-130` — `None` 值仍写入 actions

```python
if values[field] is not None:
    value = config_fields[field][Configuration.FIELD_TYPE](values[field])

if entry_mode:
    entry_key = (values['match'], values['ext'])
    self.actions.setdefault(entry_key, dict())
    self.actions[entry_key][field] = value   # value 可能是 None
```

`_add_entry` 在必填字段缺失时已经 `return`，所以正常路径上不会走到这里；但若插件 schema 添加了非必填、且配置里写成 `field=`（空值）仍会进入。建议：缺值时跳过写入，或显式 `continue`。

---

### 2.20 `configuration.py:171, 175, 178, 183` — `match.groups(1)` 用法非法

```python
if not match or match.groups(1)[0] not in config_fields:
    ...
if entry_mode and not config_fields[match.groups(1)[0]][Configuration.FIELD_MODE]:
    ...
values[match.groups(1)[0]] = match.groups(1)[1]
```

`re.Match.groups()` 不接受参数。`match.groups(1)` 在 Python 3 中是 `TypeError`。应改为 `match.group(1)` / `match.groups()[0]`。

---

### 2.21 `configuration.py:142-143` — 插件字段合并可能覆盖内置 schema

```python
config_fields = dict(Configuration.CONFIG_FIELDS)
config_fields.update(Plugins.config_fields())
```

如果插件和内置字段同名（很常见，比如 `cmd`、`ext`、`match`），插件的 schema 会被内置默认值静默覆盖，反之亦然。建议 merge 时按元组整体替换，并发出警告。

---

### 2.22 `provenance/management.py:32` — `OP_ALL` 命名易误导

```python
(OP_IO, OP_ACCESS, OP_CHMOD, OP_CHOWN, OP_ATTR, OP_GETDIR, OP_GETLINK, OP_MKNOD, OP_RMDIR,
 OP_MKDIR, OP_STATS, OP_UNLINK, OP_MKSYM, OP_MKHARD, OP_MOVE, OP_TIME, OP_CD, OP_TRUNCATE) = [2**x for x in range(18)]
OP_ALL = 2**19 - 1
```

数值上是正确的（`2**19 - 1` 覆盖到 2**17），但 `OP_ALL` 名字暗示"全部 op 位"，实际它还包含若干未定义的位（2**18）。建议改为 `OP_MASK_ALL = (1 << len(...)) - 1`。

---

### 2.23 `provenance/management.py:71-84` — `process` 表 schema 与 insert 不一致

`db_vals["process"]` 列了 18 个字段，但 `process_record.py:271-275` 实际写入 20 个值：

```python
values = (self.management.system_name, self.pstart, self.pid, self.parent_start, self.parent_pid, self.cmd, self.exe,
          self.md5, self.cwd, self.tgid_start, self.tgid, self.session_start, self.session_id, self.env,
          self.stdio[0], self.stdio[1], self.stdio[2], self.stdio_trunc[1], self.stdio_trunc[2], self.management.mid)
cursor.execute("REPLACE INTO process VALUES ({})".format(",".join(["?"] * 20)), values)
```

`db_vals["process"]` 缺 `trunc_stdout`、`trunc_stderr`、`mid`。这会导致 schema 创建时少 3 列，INSERT 时给 20 个值 → sqlite 抛 `too many values`。需要把 `trunc_stdout` / `trunc_stderr` / `mid` 加到 `db_vals["process"]`，并把 `db_keys["process"]` 同步补全。

---

### 2.24 `provenance/process_record.py:67, 246` — `trunc_history` 清空时无锁

```python
self.trunc_history.clear()
```

`_update` 可能在多线程下并发执行，clear 没有持锁。和 2.17 的 TOCTOU 同类问题。

---

### 2.25 `provenance/process_record.py:83` — cmdline 处理 off-by-one

```python
def _get_cmd(self):
    try:
        with open("/proc/{0}/cmdline".format(self.pid), "r") as handle:
            cmd = handle.read()[:-1]
    except PermissionError: cmd = ""
    return cmd
```

procfs cmdline 以 `\0` 分隔并以 `\0` 结尾。`[:-1]` 永远只剥一个字符：

- 当 cmdline 没有参数（单个 `\0`）→ `read()` 返回 `b"\0"`，`[:-1]` 得到 `b""` ✓；
- 当有一个参数 → `b"arg\0"` → `b"arg"` ✓；
- 当 N 个参数 → `b"a\0b\0...\0"` → 只剥最后一个 `\0`，剩下 `b"a\0b\0..."` 不正确。

而且与 `self.cmd == self._get_cmd()`（第 141 行）反复比较时，每次 `_get_cmd` 会剥掉一个字符，直到完全比上 —— 看上去"能跑"，但本质是巧合。

建议：

```python
cmd = handle.read().split(b'\x00')
cmd = b' '.join(c for c in cmd if c).decode('utf-8', errors='replace')
```

---

### 2.26 `provenance/process_record.py:172` — 注释引用不存在的变量

```python
# self.size = (self.process_next_block - 1) * self.core.configuration.values["block_size"] + block_size
```

`process_next_block` 和 `self.core` 在当前作用域都不存在，注释里写的是思路草稿。应删。

---

### 2.27 `provenance/replication.py:73-83` — shell 命令拼接不安全

```python
for arg in processes[process_id]["cmd"][1:]:
    if " " not in arg:
        command += " {}".format(arg)
    else:
        if "\"" in arg:
            command += " '{}'".format(arg)
        else:
            command += " \"{}\"".format(arg)
```

只考虑了两种引号嵌套：含 `"` 的用单引号，否则用双引号；同时含 `'` 和 `"` 的参数会被错误拼装。建议：

- 使用 `shlex.quote(arg)`，或
- 用 `subprocess.run([...])` 列表传参（需要把整个 pipeline 改成 shell=False + 显式连接 pipe）。

---

### 2.28 `provenance/replication.py:432` — 错误的 `is None` 判断

```python
def action_replicate(self):
    if self.api_out.respond is None:
        return
```

`self.api_out.respond` 是绑定方法对象，绑定到实例上以后从来不会是 `None`。看起来是想判断 `self.api_out is None`，但即便如此，从 `api_receive` 调过来时 `api_out` 也不为 `None`。逻辑写错但目前不抛错。

---

### 2.29 `provenance/render_graphviz.py:53` — 硬编码元组索引

```python
io_file = io_id[3:]
io_process = replication.trace_session_child(io_id[:3], session_chains)
```

`io_id` 是 `(phost, pstart, pid, path, fcreate)` 5-tuple，靠 `[:3]` / `[3:]` 切片。结构稳定没问题，但耦合 `db_keys["read"]`，任何字段顺序调整都会悄无声息地坏。建议显式命名：

```python
phost, pstart, pid, path, fcreate = io_id
```

---

### 2.30 `provenance/render_graphviz.py:91-94` — 残留开发者路径替换

```python
label = " ".join(graph["process"][process_id]["cmd"])
label = label.replace("/home/anthonyw", "/opt")
```

把 `/home/anthonyw` 替换成 `/opt` 是原作者本地路径的脱敏逻辑，对其他用户是错误的改写。应移除或改为可配置。

---

### 2.31 `provenance/graph.py:65, 71, 79` — 残留调试 print

```python
print(mount_lookup)
...
print(lineage)
...
print("Mount Paths:", process_id, mount_paths)
```

未清理的 debug 输出，会污染 stdout/stderr。建议删除或改成 logger。

---

### 2.32 `provenance/graph.py:84` — `common_mount` 切片会 IndexError

```python
for entry in graph["file"].values():
    entry["paths"] = {"abs_real": entry["path"], "rel_mount": entry["path"][len(common_mount) + 1:]}
```

如果 `entry["path"]` 不以 `common_mount` 开头，`entry["path"][len(common_mount) + 1:]` 会抛 `IndexError`（短字符串切片时不会抛，但 `common_mount` 可能比 path 还长）。建议 `entry["path"].removeprefix(common_mount + os.sep)`。

---

### 2.33 `provenance/graph.py:99-100` — `entry["env"]` / `entry["cmd"]` 可能是 `None`

```python
for field in ("cmd", "env", "stdin", "stdout", "stderr"):
    entry[field] = entry[field].replace(mount, "$$$")

entry["cmd"] = entry["cmd"].split("\0")
```

`process_record._update` 在 PermissionError 时 `self.env = ""`，但 `cmd` 在 PermissionError 时也是 `""`，`split("\0")` 会返回 `[""]`。基本不会崩，但如果 schema 把 nullable 列设为 None，会在这里 `AttributeError`。建议统一空字符串。

---

### 2.34 `provenance/graph.py:188` — str/int 混用导致键不匹配

```python
if lineage_row["parent_pid"] == 0: break   # 整数比较

...
lineage_id = (lineage_row["phost"], str(lineage_row["parent_start"]), str(lineage_row["parent_pid"]))
```

`lineage_id` 的元素全部 `str()` 化，但其它地方（如 `db_keys["process"]` 用作主键）sqlite 取出来仍然是原始类型。前后键的字符串化口径不一致，会让 dict 查找大量 miss。建议要么全 `str()`，要么全不 `str()`，统一即可。

---

## 三、风格 / 健壮性（次要）

- `core.py:583` API 分支应当 `return 0` 而非 `pass`。
- `cache_entry.py:393` 死字段 `end` 应清理或使用。
- `process_io.py:48` `BufferedReader` 路径的 `finally: notifyAll()` 不必要。
- `fuse.py:105` `{3},{4}` 之间补一个空格。
- `configuration.py:171` 等 `match.groups(1)` 调用方式需修正为 `match.group(1)`。
- `provenance/graph.py:65, 71, 79` 删掉 debug print。

---

## 四、建议优先修复顺序

| # | 文件 | 问题 | 优先级 |
|---|---|---|---|
| 1 | `provenance/graph_old.py` | 整个文件无法 import | P0 |
| 2 | `core.py:485` | O_DIRECT 优先级错误 | P0 |
| 3 | `core.py:593` | `os.truncate` 用错参数 | P0 |
| 4 | `routing.py:15-25` | 未定义 `func` / `arg1` | P0 |
| 5 | `configuration.py:171, 175, 178, 183` | `match.groups(1)` 用法非法 | P0 |
| 6 | `provenance/management.py:71-84` vs `process_record.py:271-275` | process 表 schema 与 insert 列数不一致 | P0 |
| 7 | `provenance/process_record.py:430` | `derived_soruce` 拼写错误 | P0 |
| 8 | `core.py:521-526, 553` | 异常分支 `read_buf` / `write_len` 未初始化 | P1 |
| 9 | `api.py:51-58` | 行读取器没处理 EOF | P1 |
| 10 | `provenance/graph.py:65, 71, 79` | debug print | P1 |
| 11 | `provenance/render_graphviz.py:91-94` | 硬编码 `/home/anthonyw` | P1 |
| 12 | `core.py:522, 553` | 短读 / 短写未循环 | P1 |
| 13 | `descriptor_entry.py:97-99` | 双重 TOCTOU | P2 |
| 14 | `provenance/replication.py:73-83` | shell 命令拼接不安全 | P2 |
| 15 | `provenance/process_record.py:83` | cmdline off-by-one | P2 |
| 16 | `core.py:127-128` | virt_mtime 比较顺序 | P2 |
| 17 | `provenance/graph.py:84` | `common_mount` 切片可能越界 | P2 |
| 18 | `provenance/graph.py:99-100, 188` | None / str 键不一致 | P2 |
| 19 | `descriptor_entry.py:32-44` | pipe 不进 _file_lookup | P2 |
| 20 | 其余 2.x 项目 | 见各条目 | P3 |

---

## 五、备注

- 本次审查未覆盖 `plugins/`、`build/`、`scripts/`（脚本主体一行 stub，无内容）。
- `provenance/graph_old.py` 从结构和 `repeat_test` 的 `derived_soruce` 拼写来看，是被替换前的旧实现遗留，建议作为单独清理任务处理。
- 报告完成时间 2026-08-01，基于工作分支 `dev.2025.07.01.distributed` 的最新工作区。