# Linux sing-box 自动部署与使用

本项目支持机场订阅自动解析和本地配置文件上传解析，两条路径会合并去重成一份最终的配置文件，供 `sing-box` 使用。
部分机场会拦截 Linux 的请求，或云服务器无法与机场订阅握手，导致订阅链接无法读取和解析，这属于外部限制，非本项目问题。推荐机场： [山海](https://shanhai.me/#/dashboard) 。

## 一、连接远程云服务器

通过 Termius、VS Code 等终端工具连接远程云服务器

## 二、下载自动化脚本和分流规则

```bash
curl -fsSL https://gh-proxy.org/https://raw.githubusercontent.com/hejch20108/linux-proxy/main/sing_box_auto.py -o 'sing_box_auto.py' && \
curl -fsSL https://gh-proxy.org/https://raw.githubusercontent.com/hejch20108/linux-proxy/main/custom_config.yaml -o "custom_config.yaml"
```

验证是否下载成功

```bash
ls -l sing_box_auto.py custom_config.yaml
```

## 三、首次部署

```bash
sudo install -d -m 755 /etc/apt/sources.list.d && \
sudo python3 sing_box_auto.py deploy
```

`deploy` 会完成：

1. 检查 Debian/Ubuntu、CPU 架构和 systemd；
2. 安装 Python、PyYAML、curl 和 sing-box；
3. 创建 `/etc/sing-box-auto`、`/var/lib/sing-box-auto`；
4. 安装 `sing-box-auto.service` 和可选更新 timer；
5. 将脚本复制到 `/usr/local/libexec/sing-box-auto.py`。

部署过程中会显示 `[阶段 1/7]` 到 `[阶段 7/7]`，每个阶段结束时显示耗时。APT 安装阶段会保留包管理器输出；如果某一阶段长时间没有结束，可以根据最后一个阶段判断卡在环境检查、软件包下载、订阅输入还是 systemd 配置。

如果没有保存过订阅，脚本会交互式询问：

1. `请输入或粘贴订阅链接...`：粘贴订阅链接，输入内容不会回显，按回车提交；
2. `请给你刚才输入的订阅链接起个名字...`：输入 `shanhai`；
3. 再次询问订阅链接时直接按回车结束。

> **建议优先尝试机场订阅地址能否解析成功，输入一个订阅链接，也可以连续输入多个订阅。如果无法解析成功，再考虑本地配置上传的方案**

## 四、优先使用订阅链接

部署完成后，在 Linux 服务器执行：

```bash
sudo python3 /usr/local/libexec/sing-box-auto.py update
```

`update` 会读取订阅、解析节点、下载规则集、生成最终配置、执行 `sing-box check`，然后重启服务。
订阅下载每次尝试的超时为 10 秒；失败时不会覆盖已有的最终配置。

如果第 4 阶段显示订阅来源不可用，或云服务器无法访问订阅入口，转到下一节的本地上传方案。

## 五、订阅失败时上传本地配置

本节的操作分为 **Mac 本地终端** 和 **Linux 服务器终端**。不要在 Linux 上用编辑器手工修改
`subscriptions.yaml`；在 Mac 准备好文件后上传，服务器只执行 `install` 命令。

### 5.1 在 Mac 准备本地配置文件

如果已经有本地 Sing-box JSON，例如项目中的 `config_files/shanhai.json`，可以直接上传；也可以先通过 Karing 的 SOCKS5 下载订阅正文。端口以实际配置为准，历史上常见为 `7892`：

```bash
curl -x socks5h://127.0.0.1:7892 \
  -fsSL -A 'sing-box 1.12.0' \
  'https://YOUR_SUBSCRIPTION_HOST/YOUR_SUBSCRIPTION_PATH' \
  -o /tmp/shanhai.body
```

### 5.2 从 Mac 上传到服务器临时目录

下面的路径只是示例，替换为实际服务器地址和本地文件路径；不要把订阅正文粘贴到聊天中：

```bash
scp /path/to/shanhai.json \
  ubuntu@SERVER_IP:/tmp/shanhai.json
```

如果使用刚才通过 Karing 下载的正文：

```bash
scp /tmp/shanhai.body \
  ubuntu@SERVER_IP:/tmp/shanhai.body
```

可以重复上传多个文件，例如 `shanhai.json` 和 `yep.json`。每个文件都要有不同的目标文件名。

### 5.3 在 Linux 安装本地配置文件

以下命令把临时上传文件移动到服务使用的受保护目录，并设置 `root:sing-box` 和 `0640` 权限：

```bash
sudo install -d -m 0770 -o root -g sing-box \
  /var/lib/sing-box-auto/subscriptions-cache

sudo install -m 0640 -o root -g sing-box \
  /tmp/shanhai.json \
  /var/lib/sing-box-auto/subscriptions-cache/shanhai.json

rm -f /tmp/shanhai.json
```

如果上传的是 `shanhai.body`，将上面命令中的文件名相应替换为 `shanhai.body`。

### 5.4 在 Mac 准备来源清单并上传

在 Mac 本地创建一个不含订阅正文的清单文件：

```bash
cat > /tmp/subscriptions-local.yaml <<'EOF'
subscriptions:
  - name: shanhai-local
    source: file
    path: /var/lib/sing-box-auto/subscriptions-cache/shanhai.json
EOF
```

如果第 5.3 步安装的是 `shanhai.body`，这里的 `path` 也必须写成
`/var/lib/sing-box-auto/subscriptions-cache/shanhai.body`。清单中的路径必须和服务器上的实际文件名完全一致。

如果有多个本地文件，继续添加条目，例如：

```yaml
subscriptions:
  - name: shanhai-local
    source: file
    path: /var/lib/sing-box-auto/subscriptions-cache/shanhai.json
  - name: yep-local
    source: file
    path: /var/lib/sing-box-auto/subscriptions-cache/yep.json
```

如果还要合并一个可访问的订阅链接，可以在这个清单中继续加入 `url: https://...` 条目；不要把真实链接、token 或节点内容写进项目仓库。

将清单上传到服务器临时目录：

```bash
scp /tmp/subscriptions-local.yaml \
  ubuntu@SERVER_IP:/tmp/subscriptions-local.yaml
rm -f /tmp/subscriptions-local.yaml
```

### 5.5 在 Linux 安装来源清单并生成配置

```bash
sudo install -m 0600 -o root -g root \
  /tmp/subscriptions-local.yaml \
  /etc/sing-box-auto/subscriptions.yaml

rm -f /tmp/subscriptions-local.yaml

sudo python3 /usr/local/libexec/sing-box-auto.py update
```

本地文件、订阅链接和多个本地文件会统一解析，按完整节点参数去重，最后只生成一份：

```text
/etc/sing-box-auto/config.json
```

sing-box 服务始终读取这份最终配置。若所有来源暂时不可用，会复用之前通过校验的最终配置；若只有部分来源可用，更新会停止并保留旧配置，避免静默丢失节点。若没有任何可用来源且不存在可用旧配置，会明确报错。

## 六、检查服务和代理

```bash
sudo systemctl enable --now sing-box-auto
sudo systemctl status sing-box-auto --no-pager
sudo ss -lntp | grep ':1080'
```

入口默认只监听：

```text
127.0.0.1:1080
```

服务器本机测试代理：

```bash
curl -x socks5h://127.0.0.1:1080 \
  --max-time 20 \
  -I https://www.gstatic.com/generate_204
```

返回 HTTP 204 或其他成功响应，表示代理链路正常。

## 七、测试分流规则

下面的命令使用 HTTP 代理访问域名，避免域名先在本机解析成 IP，导致域名规则无法匹配。命令只输出每条规则的 PASS/FAIL、HTTP 状态和实际路由类型。

```bash
run_case() {
    local name="$1"
    local url="$2"
    local expected="$3"
    local host="${url#https://}"
    local start
    local http_code
    local route

    start="$(date --iso-8601=seconds)"
    http_code="$(curl -x http://127.0.0.1:1080 \
      --max-time 15 -sS -o /dev/null \
      -w '%{http_code}' -I "$url" 2>/dev/null || true)"
    route="$(sudo journalctl -u sing-box-auto --since "$start" --no-pager -o cat 2>/dev/null \
      | grep "$host" | grep -E 'outbound/(block|direct|anytls|vless)' | tail -1 || true)"

    case "$expected" in
      block)  ok="$(printf '%s' "$route" | grep -q 'outbound/block' && echo yes || echo no)"; label="block" ;;
      direct) ok="$(printf '%s' "$route" | grep -q 'outbound/direct' && echo yes || echo no)"; label="direct" ;;
      proxy)  ok="$(printf '%s' "$route" | grep -Eq 'outbound/(anytls|vless)' && echo yes || echo no)"; label="proxy" ;;
    esac

    if [ "$ok" = yes ]; then
      echo "PASS  $name  [$label]  HTTP=$http_code"
    else
      echo "FAIL  $name  [期望 $label]  HTTP=$http_code"
      echo "      最近路由: ${route:-未找到}"
    fi
}

run_case "广告拦截" "https://ads.google.com" block
run_case "美国 AI" "https://openai.com" proxy
run_case "新加坡" "https://ocbc.com" proxy
run_case "台湾" "https://www.okx.com" proxy
run_case "英国" "https://www.gov.uk" proxy
run_case "国内直连" "https://www.baidu.com" direct
run_case "默认代理" "https://www.wikipedia.org" proxy
```

查看当前 `all_auto` 的实际出口 IP：

```bash
curl -x http://127.0.0.1:1080 \
  --max-time 20 \
  -fsS https://api.ipify.org
echo
```

当前 `us_auto`、`sg_auto` 等区域组使用 URLTest，日志会显示最终选中的节点。若某个区域组只有一个节点，日志只能确认走了代理节点，不能仅凭节点名称区分它属于哪个 URLTest 组。

## 八、日常更新来源

订阅链接模式：

```bash
sudo python3 /usr/local/libexec/sing-box-auto.py update
```

本地文件模式：先在 Mac 重新上传更新后的配置文件到服务器，再重复“5.3 安装本地配置文件”和“5.5 安装来源清单”，最后执行上面的 `update`。仅在 Linux 上重复执行 `update` 不会从 Mac 自动获取新文件。

查看服务日志：

```bash
sudo journalctl -u sing-box-auto -e --no-pager
```

配置更新采用临时文件校验和原子替换。校验或重启失败时会尝试恢复旧配置。

## 九、启用自动更新

自动更新默认关闭。订阅链接模式确认手动更新和代理测试稳定后，才建议启用：

```bash
sudo systemctl enable --now sing-box-auto-update.timer && \
sudo systemctl list-timers sing-box-auto-update.timer && \
sudo systemctl status sing-box-auto-update.timer --no-pager && \
sudo journalctl -u sing-box-auto-update.service -e --no-pager
```

本地文件模式不会自动从 Mac 上传新文件，因此不建议仅为本地文件模式启用 timer。

## 十、服务管理

```bash
sudo systemctl start sing-box-auto
sudo systemctl stop sing-box-auto
sudo systemctl restart sing-box-auto
sudo systemctl enable sing-box-auto
sudo systemctl disable sing-box-auto
sudo systemctl status sing-box-auto --no-pager
```

## 十一、持久化代理

写入 `~/.bashrc`

```bash
cat >> ~/.bashrc <<'EOF'

# sing-box 本机代理
export http_proxy='http://127.0.0.1:1080'
export https_proxy='http://127.0.0.1:1080'
export all_proxy='socks5://127.0.0.1:1080'
export HTTP_PROXY="$http_proxy"
export HTTPS_PROXY="$https_proxy"
export ALL_PROXY="$all_proxy"
EOF

source ~/.bashrc
```

检查变量是否生效：

```bash
env | grep -iE '^(http|https|all)_proxy='
```


## 十二、回滚

如果更新失败且目录中保留了本次更新前的配置备份，可以尝试：

```bash
sudo python3 /usr/local/libexec/sing-box-auto.py rollback
```

如果没有可用备份，先查看日志，不要删除 `/etc/sing-box-auto` 或 `/var/lib/sing-box-auto`。

## 十三、配置和权限

```text
/etc/sing-box-auto/subscriptions.yaml  订阅来源（URL 或本地文件），root:root，0600
/etc/sing-box-auto/custom_config.yaml   分流规则
/etc/sing-box-auto/config.json          生成配置，root:sing-box，0640
/var/lib/sing-box-auto/rulesets/        .srs 规则集缓存
/var/lib/sing-box-auto/subscriptions-cache/ 本地订阅正文，root:sing-box，目录 0770，文件 0640
/var/backups/sing-box-auto/             更新过程中的配置备份
/usr/local/libexec/sing-box-auto.py     systemd 使用的脚本副本
```

服务由 `sing-box` 用户运行。更新脚本会自动设置生成配置和规则集的读取权限，以及运行缓存目录的写入权限。

## 十四、支持范围

支持识别：

- Sing-box JSON；
- Clash/Mihomo YAML；
- 明文分享链接；
- 外层 Base64 分享链接。

首版生成支持 AnyTLS 和 VLESS。未知格式、缺少必要字段或暂不支持协议时会停止生成，不会静默跳过节点。

GitHub 相关规则集下载统一经过 `gh-proxy.org`。默认规则使用 SagerNet 的 `geoip-cn.srs` 和 `geosite-cn.srs`。

默认不会把代理入口暴露到公网。如需远程客户端访问，必须另行设计认证、防火墙和 TLS，不要直接暴露无认证 mixed 入口。

## 十五、可选删除

首次部署完成并确认 `update` 成功后，原始下载文件可以删除：

- `sing_box_auto.py` 会复制到 `/usr/local/libexec/sing-box-auto.py`；
- `custom_config.yaml` 会复制到 `/etc/sing-box-auto/custom_config.yaml`。

确认服务正常后，在当前下载目录执行：

```bash
rm -f sing_box_auto.py custom_config.yaml
```

如果需要修改分流规则，请在 Mac 本地修改项目中的 `custom_config.yaml`，然后上传并安装：

```bash
# Mac 本地终端
scp /path/to/custom_config.yaml ubuntu@SERVER_IP:/tmp/custom_config.yaml

# Linux 服务器终端
sudo install -m 0640 -o root -g sing-box \
  /tmp/custom_config.yaml \
  /etc/sing-box-auto/custom_config.yaml
rm -f /tmp/custom_config.yaml
sudo python3 /usr/local/libexec/sing-box-auto.py update
```
