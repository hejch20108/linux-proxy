# Linux sing-box 自动部署与使用

部分机场会拦截 Linux 的请求，当前验证成功的机场为： [山海](https://shanhai.me/#/dashboard) 。

## 一、连接远程云服务器

通过 termius、VS Code 等工具连接远程云服务器

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

至少需要输入一个订阅链接，也可以连续输入多个订阅。

## 四、生成配置并启动

部署准备完成后必须执行：

```bash
sudo python3 /usr/local/libexec/sing-box-auto.py update
```

`update` 会下载订阅、识别格式、解析 AnyTLS/VLESS 节点、下载规则集、生成配置、执行 `sing-box check`，然后重启服务。任何关键步骤失败都会停止更新并保留旧配置。

更新过程中会显示 `[阶段 1/8]` 到 `[阶段 8/8]`，包括订阅下载、节点解析、规则集下载、配置校验和服务重启。

## 五、检查服务和代理

```bash
sudo systemctl enable --now sing-box-auto && \
sudo systemctl status sing-box-auto --no-pager && \
sudo ss -lntp | grep ':1080'
```

入口默认只监听：

```text
127.0.0.1:1080
```

服务器本机测试代理：

```bash
curl -x socks5://127.0.0.1:1080 \
  --max-time 20 \
  -I https://www.gstatic.com/generate_204
```

返回 HTTP 204 或其他成功响应，表示代理链路正常。

## 六、测试分流规则

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

## 七、日常更新订阅

手动更新：

```bash
sudo python3 /usr/local/libexec/sing-box-auto.py update
```

查看服务日志：

```bash
sudo journalctl -u sing-box-auto -e --no-pager
```

配置更新采用临时文件校验和原子替换。校验或重启失败时会尝试恢复旧配置。

## 八、启用自动更新

自动更新默认关闭。确认手动更新和代理测试稳定后，再启用：

```bash
sudo systemctl enable --now sing-box-auto-update.timer && \
sudo systemctl list-timers sing-box-auto-update.timer && \
sudo systemctl status sing-box-auto-update.timer --no-pager && \
sudo journalctl -u sing-box-auto-update.service -e --no-pager
```

## 九、服务管理

```bash
sudo systemctl start sing-box-auto
sudo systemctl stop sing-box-auto
sudo systemctl restart sing-box-auto
sudo systemctl enable sing-box-auto
sudo systemctl disable sing-box-auto
sudo systemctl status sing-box-auto --no-pager
```

## 十、持久化代理

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


## 十一、回滚

如果更新失败且目录中保留了本次更新前的配置备份，可以尝试：

```bash
sudo python3 /usr/local/libexec/sing-box-auto.py rollback
```

如果没有可用备份，先查看日志，不要删除 `/etc/sing-box-auto` 或 `/var/lib/sing-box-auto`。

## 十二、配置和权限

```text
/etc/sing-box-auto/subscriptions.yaml  订阅 URL，0600
/etc/sing-box-auto/custom_config.yaml   分流规则
/etc/sing-box-auto/config.json          生成配置，root:sing-box，0640
/var/lib/sing-box-auto/rulesets/        .srs 规则集缓存
/var/backups/sing-box-auto/             更新过程中的配置备份
/usr/local/libexec/sing-box-auto.py     systemd 使用的脚本副本
```

服务由 `sing-box` 用户运行。更新脚本会自动设置生成配置和规则集的读取权限，以及运行缓存目录的写入权限。

## 十三、支持范围

支持识别：

- Sing-box JSON；
- Clash/Mihomo YAML；
- 明文分享链接；
- 外层 Base64 分享链接。

首版生成支持 AnyTLS 和 VLESS。未知格式、缺少必要字段或暂不支持协议时会停止生成，不会静默跳过节点。

GitHub 相关规则集下载统一经过 `gh-proxy.org`。默认规则使用 SagerNet 的 `geoip-cn.srs` 和 `geosite-cn.srs`。

默认不会把代理入口暴露到公网。如需远程客户端访问，必须另行设计认证、防火墙和 TLS，不要直接暴露无认证 mixed 入口。

## 十四、可选删除

首次部署完成并确认 `update` 成功后，原始下载文件可以删除：

- `sing_box_auto.py` 会复制到 `/usr/local/libexec/sing-box-auto.py`；
- `custom_config.yaml` 会复制到 `/etc/sing-box-auto/custom_config.yaml`。

确认服务正常后，在当前下载目录执行：

```bash
rm -f sing_box_auto.py custom_config.yaml
```

后续如果需要维护或者修改配置文件，可以执行如下命令：


```bash
nano /etc/sing-box-auto/custom_config.yaml
```
