# shanhai 本地订阅缓存方案交接提示

请在新 session 中继续本项目。目标是绕过腾讯云服务器无法直接访问 shanhai 订阅入口的问题，采用“Mac 下载订阅，SSH 上传到 Linux，Linux 从本地缓存解析”的方案。

## 当前背景

- 项目目录：`/Users/jingcheng/ai/proxy`
- 服务器工作目录：`/opt/sing-box-auto`
- Linux 配置：`/etc/sing-box-auto/config.json`
- Linux 订阅配置：`/etc/sing-box-auto/subscriptions.yaml`
- Linux 订阅缓存目录：建议使用 `/var/lib/sing-box-auto/subscriptions-cache/`
- sing-box 服务：`sing-box-auto.service`
- 本机代理入口：`127.0.0.1:1080`
- 当前使用 shanhai；Mac Karing 能正常获取同一订阅，腾讯云直连 `205.186.64.42:443` 持续 TCP 超时。

## 已验证事实

1. Mac Karing 当前山海订阅主机是 `205.186.64.42`，直接下载返回 HTTP 200。
2. Mac 下载内容可以识别为：Sing-box JSON 27 个节点，AnyTLS 25 个、VLESS 2 个；Mihomo User-Agent 返回 Base64 分享链接，同样是 27 个节点。
3. 腾讯云服务器到 `205.186.64.42:443`：

   ```text
   connect=0
   tls=0
   HTTP=000
   exit=28
   ```

4. 腾讯云服务器访问普通 HTTPS 网站正常，所以问题集中在机场 IP 网络路径。

## 实现目标

### 1. 扩展订阅文件格式

当前格式：

```yaml
subscriptions:
  - name: shanhai
    url: https://...
```

新增本地文件模式：

```yaml
subscriptions:
  - name: shanhai
    source: file
    path: /var/lib/sing-box-auto/subscriptions-cache/shanhai.body
```

兼容要求：

- 旧的 `url` 模式继续可用；
- `source: file` 时读取 `path`，禁止再请求远程 URL；
- 本地文件仍通过现有格式识别器解析：Sing-box JSON、Clash YAML、Base64、明文分享链接；
- 文件不存在、为空、权限不足或格式无法识别时明确报错；
- 不把文件内容、token、UUID、密码写入日志。

### 2. 权限和缓存

使用目录：

```text
/var/lib/sing-box-auto/subscriptions-cache/
```

建议权限：

- 目录：`root:sing-box`、`0770` 或更严格；
- 缓存文件：`root:sing-box`、`0640`；
- 不加入 Git；
- 更新配置时保留原有原子替换和回滚逻辑。

### 3. Mac 手动上传流程

README 增加简洁步骤：

Mac 通过 Karing SOCKS5 端口下载（端口必须先确认，历史上常见为 `7892`）：

```bash
curl -x socks5h://127.0.0.1:7892 \
  -fsSL -A 'sing-box 1.12.0' \
  '用户自己的订阅链接' \
  -o /tmp/shanhai.body
```

然后上传：

```bash
scp /tmp/shanhai.body ubuntu@SERVER_IP:/tmp/shanhai.body
```

服务器安装缓存：

```bash
sudo install -d -m 0770 -o root -g sing-box \
  /var/lib/sing-box-auto/subscriptions-cache
sudo install -m 0640 -o root -g sing-box \
  /tmp/shanhai.body \
  /var/lib/sing-box-auto/subscriptions-cache/shanhai.body
rm -f /tmp/shanhai.body
sudo python3 /usr/local/libexec/sing-box-auto.py update
```

示例中的订阅链接必须使用占位符，不得写入 README 或代码。

## 推荐实现顺序

1. 只修改 `sing_box_auto.py` 的订阅读取和缓存权限逻辑；
2. 增加单元测试：本地文件读取、缺失文件、旧 URL 模式兼容、敏感内容不进入日志；
3. 更新 README，说明 Mac 下载、scp 上传、服务器 update；
4. 在 Mac 本地运行 `py_compile` 和全部 unittest；
5. 不在 Mac 上执行 Linux 部署，不需要重装腾讯云服务器；
6. 最终给出上传脚本和测试命令，不要求用户把订阅内容粘贴到聊天中。

## 完成标准

- 服务器不再需要直接访问 `205.186.64.42` 即可生成 sing-box 配置；
- `sudo python3 /usr/local/libexec/sing-box-auto.py update` 能从本地缓存解析 27 个节点；
- 配置校验、systemd 重启、权限和回滚行为保持正常；
- README 的步骤简短、可逐项执行；
- 测试通过，Git 不包含订阅缓存或真实凭据。
