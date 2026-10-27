#!/usr/bin/env python3
"""Linux sing-box 自动部署与订阅更新工具。

本文件可以在 Mac 上做解析和生成测试，部署相关命令只面向 Debian/Ubuntu Linux。
所有敏感值都不写入日志；生成的 sing-box 配置仍需要由服务账号读取，因此应
仅部署到受控服务器并限制文件权限。
"""

from __future__ import annotations

import argparse
import base64
import copy
import getpass
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - Linux 部署时由依赖检查处理
    yaml = None


ROOT = Path("/etc/sing-box-auto")
STATE = Path("/var/lib/sing-box-auto")
SUBSCRIPTION_CACHE = STATE / "subscriptions-cache"
BACKUP = Path("/var/backups/sing-box-auto")
CONFIG = ROOT / "config.json"
SUBSCRIPTIONS = ROOT / "subscriptions.yaml"
CUSTOM = ROOT / "custom_config.yaml"
LOCAL_CUSTOM = Path(__file__).resolve().with_name("custom_config.yaml")
RULESETS = STATE / "rulesets"
SCRIPT_INSTALL_PATH = Path("/usr/local/libexec/sing-box-auto.py")
SERVICE_NAME = "sing-box-auto"
SERVICE_UNIT = Path("/etc/systemd/system/sing-box-auto.service")
TIMER_UNIT = Path("/etc/systemd/system/sing-box-auto-update.timer")
MAX_SUBSCRIPTION_BYTES = 8 * 1024 * 1024
SUPPORTED_PROTOCOLS = {"anytls", "vless"}
IGNORED_CLASH_TYPES = {"direct", "reject", "dns", "selector", "url-test", "load-balance"}

DEFAULT_CUSTOM_CONFIG = """# 用户可维护的分流规则。修改后执行：sudo python3 sing_box_auto.py update
listen:
  address: 127.0.0.1
  port: 1080

urltest:
  url: https://www.gstatic.com/generate_204
  interval: 3m
  tolerance: 50
  idle_timeout: 30m

rule_sets:
  geoip-cn:
    url: https://gh-proxy.org/https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set/geoip-cn.srs
  geosite-cn:
    url: https://gh-proxy.org/https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-cn.srs

groups:
  - tag: us_auto
    region_keywords: ["美国", "美國", "US", "🇺🇸"]
  - tag: sg_auto
    region_keywords: ["新加坡", "🇸🇬", "Singapore"]
  - tag: tw_auto
    region_keywords: ["台湾", "台灣", "🇹🇼", "TW"]
  - tag: uk_auto
    region_keywords: ["英国", "英國", "🇬🇧", "UK"]

rules:
  - name: 广告拦截
    domain: [ads.google.com, googleadservices.com]
    outbound: block
  - name: 美国 AI 和指定服务
    domain: [gemini.google.com, aistudio.google.com, gfiber.com]
    domain_suffix: [.openai.com, .chatgpt.com, .claude.ai, .claude.com, .anthropic.com, .frame.claudeusercontent.com, .bridge.claudeusercontent.com, .italkbbtv.com, .tiktok.com, .ai-member.icu, .physicaladdress.com]
    outbound: us_auto
  - name: 新加坡服务
    domain: [ocbc.com]
    outbound: sg_auto
  - name: 台湾服务
    domain_suffix: [.okx.com]
    outbound: tw_auto
  - name: 英国域名
    domain_suffix: [.uk]
    outbound: uk_auto
  - name: 国内直连域名
    domain: [cesu.topway.cn]
    domain_suffix: [.topway.cn]
    outbound: direct
"""


class DeploymentError(RuntimeError):
    pass


class UnsupportedSubscription(DeploymentError):
    pass


def log(message: str) -> None:
    print(message, flush=True)


def redact_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        query = [(k, "<已脱敏>" if k.lower() in {"token", "key", "password", "passwd", "auth", "uuid"} else v)
                 for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)]
        host = parsed.hostname or "<host>"
        if parsed.port:
            host += f":{parsed.port}"
        return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path, urllib.parse.urlencode(query), ""))
    except ValueError:
        return "<订阅链接已脱敏>"


def redact_text(value: str) -> str:
    value = re.sub(r"(?i)(token|password|passwd|uuid|key|auth)=([^&\s]+)", r"\1=<已脱敏>", value)
    value = re.sub(r'(?i)((?:"|\b)(?:token|password|passwd|uuid|key|auth)(?:"|\b)\s*[:=]\s*["\']?)([^"\'\s,}]+)', r"\1<已脱敏>", value)
    value = re.sub(r"(?i)(anytls|vless|vmess|trojan|ss)://[^\s]+", r"\1://<已脱敏>", value)
    return value


def require_root() -> None:
    if os.geteuid() != 0:
        raise DeploymentError("此命令需要 root，请使用 sudo 在 Linux 服务器上执行。")


def run_command(args: list[str], *, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=check, text=True, capture_output=capture)


def stage_start(index: int, total: int, message: str) -> float:
    started = time.monotonic()
    log(f"[阶段 {index}/{total}] {message}")
    return started


def stage_done(index: int, total: int, started: float, message: str = "完成") -> None:
    elapsed = time.monotonic() - started
    log(f"[阶段 {index}/{total}] {message}（耗时 {elapsed:.1f}s）")


def detect_os() -> dict[str, str]:
    if sys.platform != "linux":
        raise DeploymentError("部署命令只能在 Linux 服务器执行；当前环境不是 Linux。")
    data: dict[str, str] = {}
    for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            data[key] = value.strip('"')
    if data.get("ID") not in {"debian", "ubuntu"} and "debian" not in data.get("ID_LIKE", "").split():
        raise DeploymentError("首版仅支持 Debian/Ubuntu 及其衍生发行版。")
    return data


def check_environment(require_sing_box: bool = False) -> None:
    detect_os()
    if platform.machine().lower() not in {"x86_64", "amd64", "aarch64", "arm64"}:
        raise DeploymentError(f"不支持的 CPU 架构: {platform.machine()}")
    if shutil.which("systemctl") is None:
        raise DeploymentError("未找到 systemctl；该方案要求 systemd。")
    missing = [x for x in ("curl", "ca-certificates") if shutil.which(x) is None and x != "ca-certificates"]
    if missing:
        raise DeploymentError("缺少命令: " + ", ".join(missing) + "。请先安装 curl 和 CA 证书。")
    if yaml is None:
        raise DeploymentError("缺少 PyYAML，请执行: apt-get install python3-yaml")
    if require_sing_box and shutil.which("sing-box") is None:
        raise DeploymentError("未找到 sing-box，请先执行 deploy 或 install。")


def ensure_dirs() -> None:
    for path, mode in (
        (ROOT, 0o750),
        (STATE, 0o770),
        (SUBSCRIPTION_CACHE, 0o770),
        (RULESETS, 0o750),
        (BACKUP, 0o700),
    ):
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(mode)


def ensure_file(path: Path, content: str, mode: int = 0o600) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")
        path.chmod(mode)


def ensure_custom_config() -> None:
    if CUSTOM.exists():
        return
    if LOCAL_CUSTOM.exists() and LOCAL_CUSTOM != CUSTOM:
        shutil.copy2(LOCAL_CUSTOM, CUSTOM)
    else:
        CUSTOM.write_text(DEFAULT_CUSTOM_CONFIG, encoding="utf-8")
    CUSTOM.chmod(0o640)


def fetch_bytes(url: str, *, timeout: int = 30, user_agent: str = "sing-box 1.12.0") -> bytes:
    # 许多机场按 User-Agent 返回不同格式；优先请求原生 sing-box JSON，
    # 仍保留对 Base64/Clash 响应的识别能力。
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_SUBSCRIPTION_BYTES + 1)
            if len(body) > MAX_SUBSCRIPTION_BYTES:
                raise DeploymentError("订阅响应超过大小限制，已停止处理。")
            if not body:
                raise DeploymentError("订阅响应为空。")
            return body
    except urllib.error.HTTPError as exc:
        raise DeploymentError(f"订阅下载失败，HTTP {exc.code}: {redact_url(url)}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DeploymentError(f"订阅下载失败: {redact_url(url)} ({type(exc).__name__})") from exc


def read_local_subscription(path_value: str, *, source: str) -> bytes:
    """读取本地订阅缓存，不把内容写入日志。"""
    path = Path(path_value).expanduser()
    if not path.exists():
        raise DeploymentError(f"订阅 {source} 的本地文件不存在: {path}")
    if not path.is_file():
        raise DeploymentError(f"订阅 {source} 的本地路径不是普通文件: {path}")
    try:
        with path.open("rb") as handle:
            body = handle.read(MAX_SUBSCRIPTION_BYTES + 1)
    except PermissionError as exc:
        raise DeploymentError(f"订阅 {source} 的本地文件无读取权限: {path}") from exc
    except OSError as exc:
        raise DeploymentError(f"订阅 {source} 的本地文件读取失败: {path} ({type(exc).__name__})") from exc
    if len(body) > MAX_SUBSCRIPTION_BYTES:
        raise DeploymentError(f"订阅 {source} 的本地文件超过大小限制，已停止处理。")
    if not body:
        raise DeploymentError(f"订阅 {source} 的本地文件为空: {path}")
    return body


def decode_base64_text(raw: bytes) -> str | None:
    text = raw.decode("utf-8", errors="replace").strip()
    compact = re.sub(r"\s+", "", text)
    if not compact or re.search(r"(?:https?|anytls|vless|vmess|trojan|ss)://", text, re.I):
        return None
    if not re.fullmatch(r"[A-Za-z0-9+/=_-]+", compact):
        return None
    try:
        decoded = base64.urlsafe_b64decode(compact + "=" * (-len(compact) % 4))
        result = decoded.decode("utf-8", errors="strict")
    except (ValueError, UnicodeDecodeError):
        return None
    return result if "://" in result else None


def normalize_tag(tag: str, fallback: str) -> str:
    tag = urllib.parse.unquote(str(tag or "")).strip() or fallback
    tag = re.sub(r"\s+", " ", tag)
    return tag[:160]


def bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).lower() in {"1", "true", "yes", "on"}


def tls_from_fields(item: dict[str, Any], server: str) -> dict[str, Any]:
    tls = item.get("tls") if isinstance(item.get("tls"), dict) else {}
    enabled = bool_value(item.get("tls"), False) if not isinstance(item.get("tls"), dict) else bool_value(tls.get("enabled"), True)
    if not enabled and not any(k in item for k in ("sni", "servername", "server-name")):
        return {"enabled": False}
    result: dict[str, Any] = {
        "enabled": True,
        "server_name": str(tls.get("server_name") or tls.get("servername") or item.get("sni") or item.get("servername") or server),
        "insecure": bool_value(tls.get("insecure", item.get("skip-cert-verify", item.get("insecure"))), False),
    }
    fp = tls.get("utls", {}).get("fingerprint") if isinstance(tls.get("utls"), dict) else None
    fp = fp or item.get("client-fingerprint") or item.get("fingerprint")
    if fp:
        result["utls"] = {"enabled": True, "fingerprint": str(fp)}
    if tls.get("alpn") or item.get("alpn"):
        result["alpn"] = tls.get("alpn") or item.get("alpn")
    return result


def transport_from_clash(item: dict[str, Any]) -> dict[str, Any] | None:
    network = str(item.get("network") or item.get("type") or "tcp").lower()
    if network in {"anytls", "vless", "tcp"}:
        network = str(item.get("network") or "tcp").lower()
    if network == "ws":
        ws = item.get("ws-opts") or {}
        transport: dict[str, Any] = {"type": "ws"}
        if ws.get("path"):
            transport["path"] = ws["path"]
        headers = ws.get("headers")
        if headers:
            transport["headers"] = headers
        return transport
    if network == "grpc":
        grpc = item.get("grpc-opts") or {}
        return {"type": "grpc", "service_name": grpc.get("grpc-service-name", "")}
    if network in {"tcp", ""}:
        return None
    raise UnsupportedSubscription(f"发现暂不支持的传输类型: {network}")


def parse_clash_proxy(source: str, item: dict[str, Any], index: int) -> dict[str, Any]:
    protocol = str(item.get("type", "")).lower()
    if protocol in IGNORED_CLASH_TYPES:
        return {}
    if protocol not in SUPPORTED_PROTOCOLS:
        raise UnsupportedSubscription(f"{source} 包含暂不支持的节点协议: {protocol or '<空>'}")
    server = item.get("server")
    port = item.get("port")
    if not server or not port:
        raise UnsupportedSubscription(f"{source} 的第 {index} 个节点缺少 server/port")
    node: dict[str, Any] = {
        "type": protocol,
        "tag": normalize_tag(item.get("name"), f"{source}-{index}"),
        "server": str(server),
        "server_port": int(port),
    }
    if protocol == "anytls":
        if not item.get("password"):
            raise UnsupportedSubscription(f"{source} 的 AnyTLS 节点缺少 password")
        node["password"] = str(item["password"])
    else:
        if not item.get("uuid"):
            raise UnsupportedSubscription(f"{source} 的 VLESS 节点缺少 uuid")
        node["uuid"] = str(item["uuid"])
        if item.get("flow"):
            node["flow"] = str(item["flow"])
    node["tls"] = tls_from_fields(item, str(server))
    transport = transport_from_clash(item)
    if transport:
        node["transport"] = transport
    return node


def parse_share_link(source: str, line: str, index: int) -> dict[str, Any]:
    parsed = urllib.parse.urlsplit(line.strip())
    protocol = parsed.scheme.lower()
    if protocol not in SUPPORTED_PROTOCOLS:
        raise UnsupportedSubscription(f"{source} 包含暂不支持的分享链接协议: {protocol}")
    if not parsed.hostname or parsed.port is None:
        raise UnsupportedSubscription(f"{source} 的第 {index} 个分享链接缺少服务器或端口")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    get = lambda key, default=None: query.get(key, [default])[0]
    node: dict[str, Any] = {
        "type": protocol,
        "tag": normalize_tag(parsed.fragment, f"{source}-{index}"),
        "server": parsed.hostname,
        "server_port": int(parsed.port),
        "tls": {
            "enabled": bool_value(get("security", "tls") != "none", True),
            "server_name": get("sni") or get("serverName") or parsed.hostname,
            "insecure": bool_value(get("insecure"), False),
        },
    }
    fp = get("fp") or get("fingerprint")
    if fp:
        node["tls"]["utls"] = {"enabled": True, "fingerprint": fp}
    if protocol == "anytls":
        if not parsed.username:
            raise UnsupportedSubscription(f"{source} 的 AnyTLS 节点缺少密码")
        node["password"] = urllib.parse.unquote(parsed.username)
    else:
        if not parsed.username:
            raise UnsupportedSubscription(f"{source} 的 VLESS 节点缺少 UUID")
        node["uuid"] = urllib.parse.unquote(parsed.username)
        if get("flow"):
            node["flow"] = get("flow")
    transport_type = get("type", "tcp")
    if transport_type == "ws":
        transport: dict[str, Any] = {"type": "ws"}
        if get("path"):
            transport["path"] = get("path")
        if get("host"):
            transport["headers"] = {"Host": get("host")}
        node["transport"] = transport
    elif transport_type == "grpc":
        node["transport"] = {"type": "grpc", "service_name": get("serviceName") or get("service_name") or ""}
    elif transport_type not in {"tcp", ""}:
        raise UnsupportedSubscription(f"{source} 包含暂不支持的传输类型: {transport_type}")
    return node


def parse_singbox_json(source: str, obj: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for index, item in enumerate(obj.get("outbounds", []), 1):
        if not isinstance(item, dict):
            continue
        protocol = str(item.get("type", "")).lower()
        if protocol in SUPPORTED_PROTOCOLS:
            node = copy.deepcopy(item)
            node["tag"] = normalize_tag(node.get("tag"), f"{source}-{index}")
            result.append(node)
        elif protocol not in {"direct", "block", "dns", "selector", "urltest", "selector"}:
            raise UnsupportedSubscription(f"{source} JSON 包含暂不支持的 outbound: {protocol}")
    return result


def classify_and_parse(source: str, raw: bytes) -> tuple[str, list[dict[str, Any]]]:
    text = raw.decode("utf-8", errors="replace").strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        obj = None
    if isinstance(obj, dict) and "outbounds" in obj:
        nodes = parse_singbox_json(source, obj)
        if not nodes:
            raise UnsupportedSubscription(f"{source} 的 Sing-box JSON 没有可用节点")
        return "sing-box-json", nodes
    if yaml is not None:
        try:
            parsed_yaml = yaml.safe_load(text)
        except Exception:
            parsed_yaml = None
        if isinstance(parsed_yaml, dict) and isinstance(parsed_yaml.get("proxies"), list):
            nodes = [parse_clash_proxy(source, item, i) for i, item in enumerate(parsed_yaml["proxies"], 1)]
            nodes = [n for n in nodes if n]
            if not nodes:
                raise UnsupportedSubscription(f"{source} 的 Clash YAML 没有可用节点")
            return "clash-yaml", nodes
    decoded = decode_base64_text(raw)
    if decoded is not None:
        text = decoded.strip()
        format_name = "base64-share-links"
    else:
        format_name = "share-links"
    nodes = []
    for index, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "://" not in line:
            continue
        nodes.append(parse_share_link(source, line, index))
    if not nodes:
        raise UnsupportedSubscription(f"{source} 无法识别为 Sing-box JSON、Clash YAML 或分享链接/Base64。")
    return format_name, nodes


def load_yaml(path: Path, default: Any) -> Any:
    if yaml is None:
        raise DeploymentError("缺少 PyYAML，请安装 python3-yaml。")
    if not path.exists():
        return default
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    return default if value is None else value


def ensure_subscription_file(*, allow_empty: bool = False) -> dict[str, dict[str, str]]:
    if SUBSCRIPTIONS.exists():
        SUBSCRIPTIONS.chmod(0o600)
    data = load_yaml(SUBSCRIPTIONS, {"subscriptions": []})
    entries = data.get("subscriptions", []) if isinstance(data, dict) else []
    subscriptions: dict[str, dict[str, str]] = {}
    for item in entries:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name = str(item["name"]).strip()
        if name.lower().startswith("https://"):
            raise DeploymentError("订阅文件中的 name 看起来填成了 URL；请将 name 改为简短名称。")
        source = str(item.get("source", "url")).strip().lower() or "url"
        if source == "file":
            path = str(item.get("path", "")).strip()
            if not path:
                raise DeploymentError(f"订阅 {name} 使用本地文件模式但缺少 path。")
            subscriptions[name] = {"source": "file", "path": path}
            continue
        if source != "url":
            raise DeploymentError(f"订阅 {name} 的 source 只支持 url 或 file。")
        url = str(item.get("url", "")).strip()
        if not url:
            raise DeploymentError(f"订阅 {name} 缺少 url。")
        if not url.lower().startswith("https://"):
            raise DeploymentError(f"订阅 {name} 不是 HTTPS URL，已停止。")
        subscriptions[name] = {"source": "url", "url": url}
    if subscriptions:
        return subscriptions
    if allow_empty:
        return {}
    log("未找到订阅链接，请输入订阅链接（最少 1 个，支持多个）。")
    while True:
        url = getpass.getpass("请输入或粘贴订阅链接（不显示内容，输入后按回车，如您已完成全部订阅链接的输入，请直接按回车结束）：").strip()
        if not url:
            break
        if not re.match(r"^https://", url, re.I):
            log("只接受 HTTPS 订阅链接。")
            continue
        default_name = f"subscription_{len(subscriptions) + 1}"
        name = input("请给你刚才输入的订阅链接起个名字（任意名字均可）：").strip() or default_name
        subscriptions[name] = {"source": "url", "url": url}
    if not subscriptions:
        raise DeploymentError("没有可用订阅链接，已停止。")
    write_yaml = {"subscriptions": [{"name": k, **v} for k, v in subscriptions.items()]}
    SUBSCRIPTIONS.write_text(yaml.safe_dump(write_yaml, allow_unicode=True, sort_keys=False), encoding="utf-8")
    SUBSCRIPTIONS.chmod(0o600)
    return subscriptions


def node_identity(node: dict[str, Any]) -> str:
    """生成不含来源名和标签的稳定节点指纹，用于跨来源合并去重。"""
    identity = copy.deepcopy(node)
    identity.pop("source", None)
    identity.pop("tag", None)
    return json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_subscription_nodes(name: str, subscription: Any) -> tuple[str, list[dict[str, Any]]]:
    """从单个远程或本地来源读取节点；不记录订阅正文。"""
    if isinstance(subscription, str):
        source_kind = "url"
        location = subscription
    elif isinstance(subscription, dict):
        source_kind = str(subscription.get("source", "url")).lower()
        location = str(subscription.get("path" if source_kind == "file" else "url", "")).strip()
    else:
        raise DeploymentError(f"订阅 {name} 配置格式无效。")
    if source_kind == "file":
        if not location:
            raise DeploymentError(f"订阅 {name} 使用本地文件模式但缺少 path。")
        raw = read_local_subscription(location, source=name)
        fmt, parsed = classify_and_parse(name, raw)
        if not parsed:
            raise DeploymentError(f"订阅 {name} 无法解析：没有可用节点")
        return fmt, parsed
    if source_kind != "url":
        raise DeploymentError(f"订阅 {name} 的 source 只支持 url 或 file。")
    if not location:
        raise DeploymentError(f"订阅 {name} 缺少 url。")
    parsed: list[dict[str, Any]] = []
    fmt = ""
    errors: list[str] = []
    for user_agent in ("sing-box 1.12.0", "mihomo/1.19.16", "Mozilla/5.0"):
        try:
            raw = fetch_bytes(location, user_agent=user_agent)
            fmt, parsed = classify_and_parse(name, raw)
            if parsed:
                break
        except (DeploymentError, UnsupportedSubscription) as exc:
            errors.append(str(exc))
    if not parsed:
        detail = errors[-1] if errors else "没有可用节点"
        raise DeploymentError(f"订阅 {name} 无法解析：{redact_text(detail)}")
    return fmt, parsed


def load_nodes(subscriptions: dict[str, Any], *, allow_all_unavailable: bool = False) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    unavailable: list[str] = []
    for name, subscription in subscriptions.items():
        try:
            fmt, parsed = read_subscription_nodes(name, subscription)
        except (DeploymentError, UnsupportedSubscription) as exc:
            if not allow_all_unavailable:
                raise DeploymentError(redact_text(str(exc))) from exc
            unavailable.append(name)
            continue
        protocols: dict[str, int] = {}
        for node in parsed:
            protocols[node["type"]] = protocols.get(node["type"], 0) + 1
            key = node_identity(node)
            if key in seen:
                continue
            seen.add(key)
            node["source"] = name
            nodes.append(node)
        log(f"订阅 {name}: 格式={fmt}，节点={len(parsed)}，协议={','.join(f'{k}:{v}' for k,v in protocols.items())}")
    if unavailable and nodes:
        names = "、".join(unavailable)
        raise DeploymentError(f"部分订阅来源不可用（{names}），为避免丢失节点已停止更新。")
    if unavailable and not nodes and allow_all_unavailable:
        log(f"当前没有可用订阅来源（{ '、'.join(unavailable) }）。")
        return []
    if not nodes:
        raise DeploymentError("所有订阅均未解析出可用节点。")
    return nodes


def require_existing_final_config() -> None:
    """无新来源时确认之前生成的最终合并配置仍可被 sing-box 使用。"""
    if not CONFIG.is_file() or CONFIG.stat().st_size == 0:
        raise DeploymentError("没有可用的订阅链接或本地配置文件，且之前不存在最终合并配置，已停止。")
    try:
        validate_config(CONFIG)
    except DeploymentError as exc:
        raise DeploymentError("没有可用的订阅链接或本地配置文件，且之前的最终合并配置无法通过校验，已停止。") from exc


def safe_tag(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")[:80] or "node"


def ensure_rulesets(custom: dict[str, Any]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for tag, item in (custom.get("rule_sets") or {}).items():
        if not isinstance(item, dict) or not item.get("url"):
            raise DeploymentError(f"规则集 {tag} 缺少 url。")
        url = str(item["url"])
        if "github.com/" in url and "gh-proxy.org/https://github.com/" not in url:
            raise DeploymentError(f"规则集 {tag} 的 GitHub URL 未使用 gh-proxy.org。")
        target = RULESETS / f"{safe_tag(str(tag))}.srs"
        temp = target.with_suffix(".tmp")
        try:
            body = fetch_bytes(url)
            if len(body) < 16:
                raise DeploymentError(f"规则集 {tag} 下载内容过小。")
            temp.write_bytes(body)
            temp.chmod(0o640)
            temp.replace(target)
        except Exception:
            temp.unlink(missing_ok=True)
            if not target.exists():
                raise
            log(f"规则集 {tag} 更新失败，继续使用现有缓存。")
        result[str(tag)] = target
    return result


def node_region(node: dict[str, Any], keywords: list[str]) -> bool:
    tag = str(node.get("tag", "")).lower()
    return any(str(k).lower() in tag for k in keywords)


def build_config(nodes: list[dict[str, Any]], custom: dict[str, Any], ruleset_paths: dict[str, Path]) -> dict[str, Any]:
    listen = custom.get("listen") or {}
    urltest = custom.get("urltest") or {}
    outbounds: list[dict[str, Any]] = []
    node_tags: list[str] = []
    for index, source_node in enumerate(nodes, 1):
        node = {k: copy.deepcopy(v) for k, v in source_node.items() if k != "source"}
        node["tag"] = f"node_{index:03d}_{safe_tag(node.get('tag', f'node-{index}'))}"
        node_tags.append(node["tag"])
        outbounds.append(node)
    outbounds.extend([{"type": "direct", "tag": "direct"}, {"type": "block", "tag": "block"}])
    groups: dict[str, list[str]] = {"all_auto": node_tags}
    for group in custom.get("groups") or []:
        if not isinstance(group, dict) or not group.get("tag"):
            continue
        keywords = group.get("region_keywords") or []
        groups[str(group["tag"])] = [tag for tag, node in zip(node_tags, nodes) if node_region(node, keywords)]
        if not groups[str(group["tag"])]:
            log(f"节点组 {group['tag']} 没有匹配节点，将在配置中省略。")
    active_groups = {tag for tag, members in groups.items() if members}
    for tag, members in groups.items():
        if not members:
            continue
        outbounds.append({"type": "urltest", "tag": tag, "outbounds": members,
                          "url": urltest.get("url", "https://www.gstatic.com/generate_204"),
                          "interval": urltest.get("interval", "3m"),
                          "tolerance": int(urltest.get("tolerance", 50)),
                          "idle_timeout": urltest.get("idle_timeout", "30m"),
                          "interrupt_exist_connections": False})
    route_rules: list[dict[str, Any]] = []
    for item in custom.get("rules") or []:
        if not isinstance(item, dict) or not item.get("outbound"):
            continue
        outbound = str(item["outbound"])
        if outbound not in {"direct", "block", *active_groups}:
            raise DeploymentError(f"自定义规则引用了不存在的 outbound: {outbound}")
        rule = {k: item[k] for k in ("domain", "domain_suffix", "domain_keyword", "ip_cidr", "rule_set") if item.get(k)}
        if rule:
            rule["outbound"] = outbound
            route_rules.append(rule)
    for tag in ("geosite-cn", "geoip-cn"):
        if tag in ruleset_paths:
            route_rules.append({"rule_set": [tag], "outbound": "direct"})
    rule_set_entries = [{"type": "local", "tag": tag, "format": "binary", "path": str(path)} for tag, path in ruleset_paths.items()]
    return {
        "$schema": "https://sing-box.sagernet.org/schema.json",
        "log": {"level": "info", "timestamp": True},
        "dns": {"servers": [{"tag": "local", "type": "local"}], "final": "local"},
        "inbounds": [{"type": "mixed", "tag": "mixed-in", "listen": str(listen.get("address", "127.0.0.1")), "listen_port": int(listen.get("port", 1080))}],
        "outbounds": outbounds,
        "route": {"auto_detect_interface": True, "rule_set": rule_set_entries, "rules": route_rules, "final": "all_auto" if node_tags else "direct"},
        "experimental": {"cache_file": {"enabled": True, "path": str(STATE / "cache.db"), "store_fakeip": False}},
    }


def write_json_atomic(path: Path, obj: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="config.", suffix=".json", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(obj, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        temp.chmod(0o640)
        return temp
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def validate_config(path: Path) -> None:
    result = run_command(["sing-box", "check", "-c", str(path)], check=False)
    if result.returncode:
        raise DeploymentError("sing-box 配置校验失败: " + redact_text((result.stderr or result.stdout).strip())[:1000])


def service_user() -> str:
    try:
        run_command(["id", "-u", "sing-box"])
        return "sing-box"
    except subprocess.CalledProcessError:
        run_command(["useradd", "--system", "--no-create-home", "--shell", "/usr/sbin/nologin", "sing-box"])
        return "sing-box"


def install_sing_box() -> None:
    if shutil.which("sing-box") is None:
        log("正在通过官方 APT 仓库安装 sing-box。")
        run_command(["apt-get", "update"], capture=False)
        run_command(["apt-get", "install", "-y", "ca-certificates", "curl", "python3", "python3-yaml"], capture=False)
        key_dir = Path("/etc/apt/keyrings")
        key_dir.mkdir(parents=True, exist_ok=True)
        run_command(["curl", "-fsSL", "https://sing-box.app/gpg.key", "-o", str(key_dir / "sagernet.asc")])
        source = Path("/etc/apt/sources.list.d/sagernet.sources")
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("Types: deb\nURIs: https://deb.sagernet.org/\nSuites: *\nComponents: *\nEnabled: yes\nSigned-By: /etc/apt/keyrings/sagernet.asc\n", encoding="utf-8")
        run_command(["apt-get", "update"], capture=False)
        run_command(["apt-get", "install", "-y", "sing-box"], capture=False)
    else:
        current = run_command(["sing-box", "version"], check=False).stdout.strip().splitlines()[0:1]
        log("已检测到 sing-box" + (f"（{current[0]}）" if current else "") + "，检查官方仓库升级。")
        run_command(["apt-get", "update"], capture=False)
        run_command(["apt-get", "install", "-y", "--only-upgrade", "sing-box"], capture=False)


def install_units() -> None:
    script_target = SCRIPT_INSTALL_PATH
    script_target.parent.mkdir(parents=True, exist_ok=True)
    if Path(__file__).resolve() != script_target.resolve():
        shutil.copy2(__file__, script_target)
    script_target.chmod(0o755)
    user = service_user()
    for path in (ROOT, RULESETS, SUBSCRIPTION_CACHE):
        run_command(["chown", f"root:{user}", str(path)])
        path.chmod(0o750)
    run_command(["chown", f"root:{user}", str(STATE)])
    STATE.chmod(0o770)
    for path in RULESETS.glob("*.srs"):
        run_command(["chown", f"root:{user}", str(path)])
        path.chmod(0o640)
    for path in SUBSCRIPTION_CACHE.iterdir():
        if path.is_file():
            run_command(["chown", f"root:{user}", str(path)])
            path.chmod(0o640)
    if CONFIG.exists():
        run_command(["chown", f"root:{user}", str(CONFIG)])
        CONFIG.chmod(0o640)
    SERVICE_UNIT.write_text(f"""[Unit]\nDescription=sing-box 自动代理服务\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\nType=simple\nUser={user}\nGroup={user}\nExecStart=/usr/bin/sing-box -D {STATE} -c {CONFIG} run\nRestart=on-failure\nRestartSec=5s\nNoNewPrivileges=true\n\n[Install]\nWantedBy=multi-user.target\n""", encoding="utf-8")
    TIMER_UNIT.write_text(f"""[Unit]\nDescription=sing-box 可选订阅更新定时器\n\n[Timer]\nOnBootSec=15min\nOnUnitActiveSec=12h\nPersistent=true\nUnit={SERVICE_NAME}-update.service\n\n[Install]\nWantedBy=timers.target\n""", encoding="utf-8")
    update_service = Path("/etc/systemd/system/sing-box-auto-update.service")
    update_service.write_text(f"""[Unit]\nDescription=更新 sing-box 订阅配置\n\n[Service]\nType=oneshot\nExecStart=/usr/bin/python3 {script_target} update --non-interactive\n""", encoding="utf-8")
    for path in (SERVICE_UNIT, TIMER_UNIT, update_service):
        path.chmod(0o644)
    run_command(["systemctl", "daemon-reload"])


def deploy() -> None:
    require_root()
    total = 7
    started = stage_start(1, total, "检查 Linux、架构和 systemd 环境")
    detect_os()
    stage_done(1, total, started)
    started = stage_start(2, total, "安装或升级 sing-box 及系统依赖")
    install_sing_box()
    stage_done(2, total, started)
    started = stage_start(3, total, "重新检查部署依赖")
    check_environment(require_sing_box=True)
    stage_done(3, total, started)
    started = stage_start(4, total, "创建配置、状态和规则集目录")
    ensure_dirs()
    stage_done(4, total, started)
    started = stage_start(5, total, "准备 custom_config.yaml 和订阅文件")
    ensure_custom_config()
    ensure_subscription_file()
    stage_done(5, total, started)
    started = stage_start(6, total, "准备 sing-box 服务账号")
    service_user()
    stage_done(6, total, started)
    started = stage_start(7, total, "安装 systemd 服务和可选更新 timer")
    install_units()
    stage_done(7, total, started)
    if not CONFIG.exists():
        log("安装准备完成，请执行 sudo python3 sing_box_auto.py update 生成并启动配置。")


def update(*, non_interactive: bool = False, dry_run: bool = False) -> None:
    require_root()
    total = 8
    started = stage_start(1, total, "检查运行环境和 sing-box")
    check_environment(require_sing_box=True)
    stage_done(1, total, started)
    started = stage_start(2, total, "检查配置和状态目录权限")
    ensure_dirs()
    stage_done(2, total, started)
    started = stage_start(3, total, "读取 custom_config.yaml 和订阅文件")
    ensure_custom_config()
    subscriptions = ensure_subscription_file(allow_empty=True)
    custom = load_yaml(CUSTOM, {})
    stage_done(3, total, started, f"读取完成（订阅 {len(subscriptions)} 个）")
    if not subscriptions:
        started = stage_start(4, total, "校验并复用已有最终合并配置")
        require_existing_final_config()
        stage_done(4, total, started)
        log(f"没有新的订阅链接或本地配置文件，继续使用已有最终合并配置：{CONFIG}")
        return
    started = stage_start(4, total, "读取并解析订阅来源")
    nodes = load_nodes(subscriptions, allow_all_unavailable=True)
    if not nodes:
        require_existing_final_config()
        stage_done(4, total, started, "没有可用来源，继续使用已有最终合并配置")
        log(f"当前来源均不可用，继续使用已有最终合并配置：{CONFIG}")
        return
    stage_done(4, total, started, f"解析完成（节点 {len(nodes)} 个）")
    started = stage_start(5, total, "下载或更新本地规则集")
    rulesets = ensure_rulesets(custom)
    stage_done(5, total, started, f"规则集完成（{len(rulesets)} 个）")
    started = stage_start(6, total, "生成临时 sing-box 配置并校验")
    generated = build_config(nodes, custom, rulesets)
    temp = write_json_atomic(CONFIG, generated)
    try:
        validate_config(temp)
        stage_done(6, total, started)
        if dry_run:
            log(f"dry-run 成功：生成节点 {len(nodes)} 个，临时配置未替换正式配置。")
            return
        started = stage_start(7, total, "原子替换配置并重启 sing-box 服务")
        previous = None
        if CONFIG.exists():
            stamp = time.strftime("%Y%m%d-%H%M%S")
            previous = BACKUP / f"config-{stamp}.json"
            shutil.copy2(CONFIG, previous)
            previous.chmod(0o600)
        temp.replace(CONFIG)
        CONFIG.chmod(0o640)
        user = service_user()
        run_command(["chown", f"root:{user}", str(CONFIG)])
        install_units()
        run_command(["systemctl", "restart", SERVICE_NAME])
        status = run_command(["systemctl", "is-active", SERVICE_NAME], check=False)
        if status.returncode:
            raise DeploymentError("新配置已写入，但服务未进入 active，开始回滚。")
        stage_done(7, total, started)
        started = stage_start(8, total, "清理临时备份并完成更新")
        # 校验和重启均成功后删除旧配置备份；失败路径会保留它供自动/显式回滚。
        if previous:
            previous.unlink(missing_ok=True)
        stage_done(8, total, started)
        log(f"更新成功：解析节点 {len(nodes)} 个，服务已重启。")
    except Exception:
        temp.unlink(missing_ok=True)
        if 'previous' in locals() and previous and previous.exists():
            shutil.copy2(previous, CONFIG)
            run_command(["systemctl", "restart", SERVICE_NAME], check=False)
            log("更新失败，已尝试恢复旧配置。")
        raise
    finally:
        temp.unlink(missing_ok=True)


def service_action(action: str) -> None:
    require_root()
    run_command(["systemctl", action, SERVICE_NAME])


def show_status() -> None:
    result = run_command(["systemctl", "status", SERVICE_NAME, "--no-pager"], check=False)
    print(redact_text(result.stdout or result.stderr))


def show_logs() -> None:
    require_root()
    result = run_command(["journalctl", "-u", SERVICE_NAME, "--output", "cat", "-e", "--no-pager"], check=False)
    print(redact_text(result.stdout or result.stderr))


def rollback() -> None:
    require_root()
    backups = sorted(BACKUP.glob("config-*.json"))
    if not backups:
        raise DeploymentError("没有可回滚的配置备份。")
    candidate = backups[-1]
    validate_config(candidate)
    shutil.copy2(candidate, CONFIG)
    run_command(["systemctl", "restart", SERVICE_NAME])
    log("已恢复最近一次备份配置。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Linux sing-box 自动部署与更新")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("deploy")
    sub.add_parser("install")
    update_parser = sub.add_parser("update")
    update_parser.add_argument("--non-interactive", action="store_true")
    update_parser.add_argument("--dry-run", action="store_true")
    sub.add_parser("check")
    for name in ("start", "stop", "restart", "enable", "disable"):
        sub.add_parser(name)
    sub.add_parser("status")
    sub.add_parser("logs")
    sub.add_parser("rollback")
    args = parser.parse_args(argv)
    try:
        if args.command in {"deploy", "install"}:
            deploy()
        elif args.command == "update":
            update(non_interactive=args.non_interactive, dry_run=args.dry_run)
        elif args.command == "check":
            check_environment(require_sing_box=False)
            if CONFIG.exists() and shutil.which("sing-box"):
                validate_config(CONFIG)
            log("环境检查通过。")
        elif args.command in {"start", "stop", "restart"}:
            service_action(args.command)
        elif args.command == "enable":
            require_root(); run_command(["systemctl", "enable", SERVICE_NAME])
        elif args.command == "disable":
            require_root(); run_command(["systemctl", "disable", SERVICE_NAME])
        elif args.command == "status":
            show_status()
        elif args.command == "logs":
            show_logs()
        elif args.command == "rollback":
            rollback()
        return 0
    except (DeploymentError, UnsupportedSubscription, subprocess.CalledProcessError) as exc:
        print(f"错误：{redact_text(str(exc))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
