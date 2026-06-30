"""AnkiConnect 共用小工具：invoke() 呼叫 + 常數定義。"""
import json
import urllib.error
import urllib.request

ANKI_URL = "http://127.0.0.1:8765"
DECK_NAME = "YouTube Mining"
MODEL_NAME = "YT Mining EN"

FIELDS = [
    "Word", "Sentence", "Definition", "Chinese", "Collocation", "Synonyms",
    "SentenceAudio", "WordAudio", "Image", "Source", "URL",
]

FRONT = """<div class="sentence">{{Sentence}}</div>
<div class="audio">{{SentenceAudio}}</div>"""

BACK = """{{FrontSide}}
<hr id=answer>
<div class="word">{{Word}}</div>
{{#Chinese}}<div class="zh">{{Chinese}}</div>{{/Chinese}}
<div class="definition">{{Definition}}</div>
{{#Collocation}}<div class="colloc">\U0001f517 {{Collocation}}</div>{{/Collocation}}
{{#Synonyms}}<div class="syn">{{Synonyms}}</div>{{/Synonyms}}
<div class="word-audio">{{WordAudio}}</div>
{{#Image}}<div class="image">{{Image}}</div>{{/Image}}
<div class="source">{{Source}} · {{URL}}</div>"""

CSS = """.card {
  font-family: -apple-system, "Helvetica Neue", sans-serif;
  font-size: 20px;
  text-align: center;
  color: #1a1a1a;
  background: #fafafa;
}
.sentence { font-size: 22px; line-height: 1.6; margin: 16px 0; }
.sentence b, .target { color: #2962ff; font-weight: 700; }
/* 視覺階層：字(大) → 中文(中,主錨點) → 英定義(小灰) → 同義字(更小淡) */
.word { font-size: 28px; font-weight: 700; margin-top: 12px; }
.zh { font-size: 21px; color: #1a1a1a; margin: 6px 0 12px; }
.definition { font-size: 15px; color: #888; margin: 6px 0; line-height: 1.45; }
.colloc { font-size: 15px; color: #00796b; margin: 6px 0; }
.syn { font-size: 13px; color: #9575cd; margin: 6px 0; line-height: 1.5; }
.syn .ant { color: #e57373; }
.image img { max-width: 62%; border-radius: 8px; margin: 12px 0; }
.source { font-size: 12px; color: #bbb; margin-top: 14px; }
hr#answer { border: none; border-top: 1px solid #ddd; margin: 14px 0; }
@media (prefers-color-scheme: dark) {
  .card { color: #e0e0e0; background: #1e1e1e; }
  .zh { color: #f0f0f0; }
  .definition { color: #999; }
}"""


def invoke(action, **params):
    payload = json.dumps({"action": action, "version": 6, "params": params}).encode()
    req = urllib.request.Request(ANKI_URL, payload)
    try:
        raw = urllib.request.urlopen(req, timeout=30).read()
    except urllib.error.URLError as ex:
        raise RuntimeError(
            f"連不上 AnkiConnect ({ANKI_URL})：請先開啟 Anki 桌面並確認已安裝 "
            f"AnkiConnect add-on。原始錯誤：{ex}"
        ) from None
    try:
        resp = json.loads(raw)
    except json.JSONDecodeError as ex:
        raise RuntimeError(f"AnkiConnect 回應非 JSON（{action}）：{raw[:200]!r}") from ex
    if not isinstance(resp, dict) or "result" not in resp:
        raise RuntimeError(f"AnkiConnect 回應格式異常（{action}）：{resp!r}")
    if resp.get("error") is not None:
        raise RuntimeError(f"AnkiConnect 錯誤（{action}）：{resp['error']}")
    return resp["result"]
