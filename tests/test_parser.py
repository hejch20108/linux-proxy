import base64
import json
import sys
import unittest
from pathlib import Path

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
