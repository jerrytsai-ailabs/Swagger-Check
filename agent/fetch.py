import os

import requests
import yaml

from .config import REQUEST_TIMEOUT_SECONDS, SPEC_FILES


def fetch_specs(base_url):
    """依 config.SPEC_FILES 從 base_url 抓下每份 YAML 並解析成 dict。

    回傳 list of dict: {group, filename, is_real, spec, raw_text, error}
    抓取或解析失敗時該筆的 spec 為 None、error 帶上錯誤訊息，其餘筆不受影響。
    """
    results = []
    for group, filename, is_real in SPEC_FILES:
        url = f"{base_url.rstrip('/')}/{filename}"
        entry = {
            "group": group,
            "filename": filename,
            "is_real": is_real,
            "url": url,
            "spec": None,
            "raw_text": None,
            "error": None,
        }
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
            resp.raise_for_status()
            # 這個站台沒有在 Content-Type 帶 charset，requests 會退回 ISO-8859-1 猜測，
            # 把多位元組的 UTF-8（中文說明文字）弄壞——一律當 UTF-8 解碼。
            text = resp.content.decode("utf-8")
            entry["raw_text"] = text
            entry["spec"] = yaml.safe_load(text)
        except Exception as exc:  # noqa: BLE001 - 蒐集所有失敗原因回報，不中斷其他檔案
            entry["error"] = str(exc)
        results.append(entry)
    return results


def load_local_specs(spec_dir):
    """離線模式：從本機資料夾讀取先前抓好的 YAML（例如 swagger-spec/），格式與 fetch_specs 相同。"""
    results = []
    for group, filename, is_real in SPEC_FILES:
        path = os.path.join(spec_dir, filename)
        entry = {
            "group": group,
            "filename": filename,
            "is_real": is_real,
            "url": path,
            "spec": None,
            "raw_text": None,
            "error": None,
        }
        try:
            with open(path, encoding="utf-8") as fh:
                entry["raw_text"] = fh.read()
            entry["spec"] = yaml.safe_load(entry["raw_text"])
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)
        results.append(entry)
    return results
