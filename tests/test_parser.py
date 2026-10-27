import base64
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
import sing_box_auto as app


class ParserTests(unittest.TestCase):
  def test_base64_anytls_and_vless(self):
    source = "demo"
    links = "\n".join([
        "anytls://secret@example.com:443/?type=tcp&insecure=1&fp=chrome#美国01",
        "vless://00000000-0000-0000-0000-000000000000@example.net:8443?security=tls&sni=example.net&type=ws&path=%2Fws#台湾01",
    ]).encode()
    raw = base64.b64encode(links)
    fmt, nodes = app.classify_and_parse(source, raw)
    self.assertEqual(fmt, "base64-share-links")
    self.assertEqual([n["type"] for n in nodes], ["anytls", "vless"])
    self.assertEqual(nodes[1]["transport"]["path"], "/ws")


  def test_clash_yaml(self):
    raw = b"""proxies:\n  - name: us\n    type: anytls\n    server: example.com\n    port: 443\n    password: secret\n    tls: true\n    sni: example.com\n"""
    fmt, nodes = app.classify_and_parse("clash", raw)
    self.assertEqual(fmt, "clash-yaml")
    self.assertEqual(nodes[0]["type"], "anytls")
    self.assertTrue(nodes[0]["tls"]["enabled"])


  def test_singbox_json(self):
    raw = json.dumps({"outbounds": [{"type": "anytls", "tag": "us", "server": "example.com", "server_port": 443, "password": "secret"}]}).encode()
    fmt, nodes = app.classify_and_parse("json", raw)
    self.assertEqual(fmt, "sing-box-json")
    self.assertEqual(nodes[0]["tag"], "us")

  def test_local_file_subscription(self):
    raw = json.dumps({"outbounds": [
        {"type": "anytls", "tag": "本地美国01", "server": "example.com", "server_port": 443, "password": "local-secret"},
    ]}).encode()
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "subscription.body"
      path.write_bytes(raw)
      output = io.StringIO()
      with mock.patch.object(app, "fetch_bytes") as fetch, redirect_stdout(output):
        nodes = app.load_nodes({
            "local": {
                "source": "file",
                "path": str(path),
                "url": "https://should-not-be-requested.example/sub?token=secret",
            },
        })
      self.assertEqual(len(nodes), 1)
      self.assertNotIn("local-secret", output.getvalue())
      fetch.assert_not_called()

  def test_local_file_subscription_missing_is_explicit(self):
    with self.assertRaisesRegex(app.DeploymentError, "本地文件不存在"):
      app.load_nodes({"local": {"source": "file", "path": "/definitely/missing.body"}})

  def test_legacy_url_subscription_shape_is_compatible(self):
    raw = json.dumps({"outbounds": [
        {"type": "anytls", "tag": "legacy", "server": "example.com", "server_port": 443, "password": "secret"},
    ]}).encode()
    with mock.patch.object(app, "fetch_bytes", return_value=raw) as fetch:
      nodes = app.load_nodes({"legacy": "https://example.test/sub?token=secret"})
    self.assertEqual(nodes[0]["tag"], "legacy")
    fetch.assert_called_once()

  def test_subscription_yaml_accepts_url_and_file_entries(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "subscriptions.yaml"
      path.write_text(
          "subscriptions:\n"
          "  - name: remote\n"
          "    url: https://example.test/sub\n"
          "  - name: local\n"
          "    source: file\n"
          "    path: /var/lib/sing-box-auto/subscriptions-cache/local.body\n",
          encoding="utf-8",
      )
      with mock.patch.object(app, "SUBSCRIPTIONS", path):
        subscriptions = app.ensure_subscription_file()
    self.assertEqual(subscriptions["remote"], {"source": "url", "url": "https://example.test/sub"})
    self.assertEqual(subscriptions["local"]["source"], "file")

  def test_merge_dedup_keeps_different_credentials(self):
    raw = json.dumps({"outbounds": [
        {"type": "anytls", "tag": "first", "server": "same.example", "server_port": 443, "password": "one"},
        {"type": "anytls", "tag": "duplicate", "server": "same.example", "server_port": 443, "password": "one"},
        {"type": "anytls", "tag": "different", "server": "same.example", "server_port": 443, "password": "two"},
    ]}).encode()
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "subscription.json"
      path.write_bytes(raw)
      nodes = app.load_nodes({"local": {"source": "file", "path": str(path)}})
    self.assertEqual([node["tag"] for node in nodes], ["first", "different"])

  def test_missing_sources_require_existing_final_config(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "config.json"
      with mock.patch.object(app, "CONFIG", path):
        with self.assertRaisesRegex(app.DeploymentError, "之前不存在最终合并配置"):
          app.require_existing_final_config()

  def test_all_unavailable_sources_can_fall_back(self):
    nodes = app.load_nodes(
        {"missing": {"source": "file", "path": "/definitely/missing.body"}},
        allow_all_unavailable=True,
    )
    self.assertEqual(nodes, [])

  def test_partial_source_failure_does_not_silently_drop_nodes(self):
    raw = json.dumps({"outbounds": [
        {"type": "anytls", "tag": "available", "server": "example.com", "server_port": 443, "password": "secret"},
    ]}).encode()
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "subscription.json"
      path.write_bytes(raw)
      with self.assertRaisesRegex(app.DeploymentError, "部分订阅来源不可用"):
        app.load_nodes({
            "available": {"source": "file", "path": str(path)},
            "missing": {"source": "file", "path": "/definitely/missing.body"},
        }, allow_all_unavailable=True)


  def test_redaction(self):
    value = "https://example.test/sub?token=secret&x=1"
    self.assertNotIn("secret", app.redact_url(value))
    self.assertNotIn("secret", app.redact_text("vless://uuid=secret"))


  def test_unsupported_protocol_fails(self):
    raw = b"vmess://abc"
    try:
        app.classify_and_parse("bad", raw)
    except app.UnsupportedSubscription:
        pass
    else:
        self.fail("unsupported protocol should fail")

  def test_build_config_has_local_inbound_and_urltests(self):
    custom = app.load_yaml(app.LOCAL_CUSTOM, {})
    nodes = [
        {"type": "anytls", "tag": "美国 01", "server": "a.example", "server_port": 443, "password": "x", "tls": {"enabled": True}},
        {"type": "anytls", "tag": "新加坡 01", "server": "b.example", "server_port": 443, "password": "x", "tls": {"enabled": True}},
        {"type": "anytls", "tag": "台湾 01", "server": "c.example", "server_port": 443, "password": "x", "tls": {"enabled": True}},
        {"type": "anytls", "tag": "英国 01", "server": "d.example", "server_port": 443, "password": "x", "tls": {"enabled": True}},
    ]
    config = app.build_config(nodes, custom, {})
    self.assertEqual(config["inbounds"][0]["listen"], "127.0.0.1")
    groups = {item["tag"] for item in config["outbounds"] if item["type"] == "urltest"}
    self.assertTrue({"all_auto", "us_auto", "sg_auto", "tw_auto", "uk_auto"}.issubset(groups))
    self.assertEqual(config["route"]["final"], "all_auto")


if __name__ == "__main__":
    unittest.main()
