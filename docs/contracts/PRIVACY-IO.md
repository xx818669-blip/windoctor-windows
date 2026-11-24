# core/1.0.0 隐私、解析与文件边界

## 比较作用域

`scope-init` 生成 32 字节密码学随机密钥 K，使用 Windows DPAPI CurrentUser 并禁止交互，将其加密。禁用 LOCAL_MACHINE，不提供明文回退、命令行/环境变量密钥或自动轮换。

scope 文件最多 16 KiB，遵循[模型与状态](MODEL.md)规定的深度、重复键、Unicode 和字段检查；仅含 schema_version=`windoctor.scope/1` 和 protected_key（DPAPI blob 的 Base64，4..4096 字符且合法 padding），不含明文 K。`--out` 指定新文件的路径。父目录须已存在，文件排他创建。真实文件放忽略的 artifacts 或用户明确选择的可信私人本地目录，不传入模型上下文、Git 或日志。

capture 必须明确 --scope-file，缺失/格式错误/不可读取/无法解密均失败，不自动生成或静默降级。解密后的 K 必须恰好 32 字节，仅在内存使用，不落明文临时文件，不承诺 Python 内存可靠清零。B/T/R 复用同一作用域，每诊断组新建 scope。scope_id=`HMAC-SHA256(K, UTF8('windoctor-scope/1'))` 的 64 位小写 hex，不由用户自设。

支持能使用同一 Windows 用户保护上下文解密的入口；不同身份或受限沙箱无法解密时返回 E_SCOPE_UNAVAILABLE，不绕过权限。无跨用户/跨机器同步、密钥服务或恢复。结束后由用户自行删除 scope；删除后旧快照仍能比较，但新复测必须重新成组采集。[DPAPI 保护机制](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)

## 展示与比较

显示值只用数据模型中的固定枚举，不显示路径、用户名、代理主机、文件名片段或异常。输出器只从已校验白名单重建数据，不盲信输入自称已脱敏。

token=`HMAC-SHA256(K, message)` 的完整小写 hex。message 为紧凑 JSON 序列 `["windoctor-compare/1",1,subject,canonical_value]` 的 UTF-8；无额外空格、ensure_ascii=false、拒绝非法 surrogate。整数 1 为 normalization 版本。同主题同 scope 同规范化值必须同 token，主题间域分离。PATH 条目用 subject=`path-entry`；exec 候选用 exec.node 等对应主题。

policy 的 privacy/normalization/resolver 都为 1 且 scope_id 相同才比较。其它情况 not_comparable，不能拿 token 或显示占位直接判断。schema 可表示未来策略数字，但本版不假装支持。

HMAC 不证明来源真实或快照未篡改；持有 K 可对低熵值作猜测。无公开盐普通 hash 回退。不承诺绝对匿名、隔离同用户恶意进程或识别任意隐蔽编码。

## 规范化 v1

路径仅 `/` 改 `\`、盘符 ASCII 大写；其余字符、重复分隔符、大小写、Unicode、`.`、`..` 都保留。不 resolve、不展开变量、不查当前文件系统。拼写或大小写差异只是词法差异，不能据此认定文件身份不同。

PATH 按分号拆分，保留空项、顺序、重复项，逐项规范化后用字符串列表作 canonical_value。不得排序去重。空 PATH 为 empty、未设置为 missing。上限为总 32767 码点、256 项、单项 4096；超限为 error/LIMIT_EXCEEDED，停止搜索，不截断后假装完整。

cwd 和确定 exec 路径用上述词法规则。PATHEXT、NO_PROXY 不解释、不裁剪或排序，原始非空字符串作 canonical_value，永不明文输出。

代理仅接受 <=4096 字符的 http/https/socks5 URL：合法主机和端口，无 query/fragment，path 仅空或 `/`，不做 DNS。scheme/ASCII 主机小写，非 ASCII 主机使用 Python 标准库 IDNA ASCII 表示，IPv6 使用标准压缩表示；缺省端口 80/443/1080。canonical_value 为 `[scheme,host,integer_port]`。先移除 userinfo，认证内容不进入 token；auth_present 只记录存在性。拒绝 host 中百分号编码、控制/空白字符、缺主机、非法/空显式端口、无法完整解析的 URL，记录 error/INVALID_VALUE，不输出原文。userinfo 可有百分号编码但从不解释或输出，不影响端点 token；仅密码变化不生成内容差异。

## Windows PATH 解析 v1

固定白名单与同目录扩展顺序：node→node.exe；python→python.exe；npx→npx.exe/npx.cmd/npx.bat；codex→codex.exe/codex.cmd/codex.bat。先 PATH 目录顺序，再扩展顺序。只检查普通文件元数据，不执行、不读脚本内容；PATHEXT 仅观测、不驱动解析。不覆盖 PowerShell alias/function、App Paths、隐式当前目录和 WSL。

预检拒绝 UNC、设备命名空间、ADS、非绝对盘符路径、含 `.`/`..`、引号的目录。空项、相对项、异常项保留索引和跳过原因。访问前分类盘符，只接触确认的本地 fixed drive；网络盘、未知类型、重解析祖先/目标或无法确认本地性的路径不跟随。支持可信且无恶意并发替换的目录，逐级检查不是抗竞态沙箱。

每程序按 `(PATH目录序号, 该程序固定扩展名序号)` 的次序检查候选。同名目录不是普通文件候选；只有确定不存在或不是普通文件可排除，访问失败不能当不存在。首个已观测普通文件之前的任一候选未确定（包括同目录较优先扩展名），则 indeterminate；整个前缀确定才 found。无候选且全部完整才 not_found。比如 npx.exe 无权限而 npx.cmd 存在，npx 仍为 indeterminate。不用 shutil.which 绕过策略。

path_entries 是目录级摘要：目录未找到为 missing；跳过为 skipped；可访问目录内所有规定候选检查完成为 searched（即使未找到程序）；存在候选检查失败时按 error 优先于 inaccessible 聚合，并保留固定原因。它不代替每程序的候选前缀判定，某程序的失败不使另一程序已经完整的前缀失效。resolution 由对应程序实际探测顺序生成，不把其它程序的候选错误套用过来。

“默认离线”指不主动发起网络请求、拒绝已识别网络路径，不承诺操作系统或过滤驱动绝无网络流量。结果只称“本策略下的 PATH 候选”，不等于 shell 实际执行结果。Diff 不访问快照中的路径。[Windows 驱动器类型](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getdrivetypew) · [重解析点](https://learn.microsoft.com/en-us/windows/win32/fileio/reparse-points)

## 输入输出

只读取 CLI 明确指定的快照、scope 和报告，不依据输入字段读别的文件、不递归发现配置。文件必须是本地 fixed drive 绝对普通文件；拒绝 UNC、设备路径、ADS、网络盘和重解析目标/祖先；无法确认则失败关闭。输出父目录必须存在，不展开变量或自动建目录。

报告输出扩展名 .json/.txt/.html，scope 为 .wdscope。输入输出同路径或已识别同文件拒绝。v1 无覆盖参数：先在内存校验、脱敏和序列化，再排他创建新文件（xb 语义），不能检查不存在后普通写入。

写入与关闭均成功才成功；失败只尝试清理由本次创建且身份仍一致的文件。清理失败 E_OUTPUT_PARTIAL，不删除已有或身份不明文件，不回显路径。本版不承诺断电事务性发布。stdout 也先完成内存序列化再输出单一文档；管道写失败返回 I/O 错误，不承诺回滚已发送字节。

JSON 输出不得超过对应输入 kind 的上限；文本/HTML 最多 4 MiB，超过则 E_INPUT_LIMIT，不写部分文件。所有格式都由共享接口 `write_output` 写入。[Python 接口](OPERATIONS.md)
