"""
统一API调用层
支持 OpenAI兼容格式 的多个provider
"""
import json
import re
import requests
from .config import get_config


# 调试模式开关
_debug_mode = False


def set_debug_mode(enabled):
    """设置调试模式开关"""
    global _debug_mode
    _debug_mode = enabled


def is_debug_mode():
    """获取调试模式状态"""
    return _debug_mode


class APIClient:
    def __init__(self, model_type="main"):
        self.cfg = get_config()
        self.model_type = model_type

    def _get_headers(self, api_key):
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

    def _extract_content(self, response_json, provider):
        """从不同provider的响应中提取文本内容"""
        try:
            if provider in ["openai", "deepseek"]:
                content = response_json["choices"][0]["message"]["content"]
            elif provider == "anthropic":
                # Claude API格式
                if "content" in response_json:
                    content = response_json["content"][0]["text"]
                else:
                    content = response_json["choices"][0]["message"]["content"]
            elif provider == "gemini":
                # Gemini通过OpenAI兼容接口
                content = response_json["choices"][0]["message"]["content"]
            else:
                content = str(response_json)
        except (KeyError, IndexError) as e:
            raise ValueError(f"解析API响应失败: {e}, 原始响应: {json.dumps(response_json, ensure_ascii=False)[:500]}")

        # 过滤 DeepSeek V4 思考标签
        if "<think" in content:
            content = re.sub(r'<think[^>]*>.*?</think\s*>', '', content, flags=re.DOTALL).strip()

        return content

    def call(self, system_prompt, user_prompt, temperature=0.7, max_retries=1, thinking=False):
        """
        调用AI模型
        参数:
            thinking: DeepSeek V4 的思考模式开关（仅对 deepseek-v4-pro/flash 有效）
        返回: (content_text, success)
        """
        model_cfg = self.cfg.get_model_config(self.model_type)
        provider = model_cfg.get("provider", "openai")
        model = model_cfg.get("model", "gpt-4o")
        api_base = model_cfg.get("api_base", "https://api.openai.com/v1")
        api_key = self.cfg.get_api_key(provider)

        if not api_key:
            return f"错误: 未设置 {provider} 的API密钥", False

        url = f"{api_base.rstrip('/')}/chat/completions"
        headers = self._get_headers(api_key)

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": temperature,
            "max_tokens": 4096
        }

        # DeepSeek V4 思考模式
        if provider == "deepseek" and thinking:
            payload["thinking"] = {"type": "enabled"}

        # 准备调试日志数据
        debug_request = {
            "url": url,
            "model": model,
            "provider": provider,
            "temperature": temperature,
            "thinking": thinking,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt
        }

        import time as _time
        last_error = None
        response_data = None
        for attempt in range(max_retries + 1):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=120)
                response_data = {
                    "status_code": resp.status_code,
                    "headers": dict(resp.headers),
                    "body_preview": resp.text[:2000] if len(resp.text) > 2000 else resp.text
                }

                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:500]}"
                    # 退避：避免429/5xx立即连打触发限流
                    if attempt < max_retries:
                        _time.sleep(0.5 * (attempt + 1))
                    continue

                data = resp.json()
                response_data["parsed_body"] = data
                try:
                    content = self._extract_content(data, provider)
                except (KeyError, IndexError, ValueError):
                    # 解析响应结构失败（如字段缺失）→ 视为失败返回，不重试（重发同一响应不会更好）
                    return f"API调用失败 (解析响应失败: {str(data)[:200]})", False
                if content is None:
                    # thinking 模式正文可能在 reasoning_content；content 为 null 视为空响应
                    last_error = "API返回空content（可能在reasoning_content中）"
                    if attempt < max_retries:
                        _time.sleep(0.5 * (attempt + 1))
                    continue

                # 调试模式：记录成功调用
                if is_debug_mode():
                    try:
                        from .ui.debug_window import log_api_call
                        log_api_call(debug_request, response_data, self.model_type, success=True)
                    except Exception:
                        pass

                return content, True

            except requests.exceptions.Timeout:
                last_error = "API请求超时"
            except requests.exceptions.ConnectionError:
                last_error = "无法连接到API服务器"
            except Exception as e:
                last_error = str(e)

        # 调试模式：记录失败调用
        if is_debug_mode():
            try:
                from .ui.debug_window import log_api_call
                debug_response = response_data or {"error": last_error}
                log_api_call(debug_request, debug_response, self.model_type, success=False)
            except Exception:
                pass

        return f"API调用失败 ({last_error})", False

    def call_json(self, system_prompt, user_prompt, temperature=0.7, max_retries=1, thinking=False):
        """
        调用AI并尝试解析JSON
        返回: (parsed_json, success)
        """
        content, ok = self.call(system_prompt, user_prompt, temperature, max_retries, thinking=thinking)
        if not ok:
            return {"error": content}, False

        # 尝试从markdown代码块中提取JSON
        text = content.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            parsed = json.loads(text)
            return parsed, True
        except json.JSONDecodeError as e:
            # 重试一次，要求纯JSON
            retry_prompt = f"""请只返回纯JSON，不要包含任何markdown代码块标记（如 ```json）。
之前的响应解析失败，错误: {e}

请重新输出以下内容的JSON格式：
{text[:1000]}
"""
            content2, ok2 = self.call(system_prompt, retry_prompt, temperature, max_retries=0, thinking=thinking)
            if ok2:
                try:
                    # 与首次解析一致：剥 markdown 代码块 fence 后再 json.loads
                    t2 = content2.strip()
                    if t2.startswith("```json"):
                        t2 = t2[7:]
                    elif t2.startswith("```"):
                        t2 = t2[3:]
                    if t2.endswith("```"):
                        t2 = t2[:-3]
                    return json.loads(t2.strip()), True
                except:
                    pass
            return {"error": f"JSON解析失败: {e}", "raw": content[:2000]}, False

    def stream(self, system_prompt, user_prompt, temperature=0.7, thinking=False):
        """
        流式调用AI模型（SSE），生成器逐块 yield。
        yield 元组：
          ("content", delta)  增量文本
          ("error", msg)      出错（HTTP非200 / SSE内错误 / 超时 / 连接失败），随后生成结束
        正常结束（data: [DONE]）后自然终止，不再 yield。
        用于 P1 主叙事流式显示；P2/P3/P4/P6/P11/P12/P13/P14 仍走非流式 call()。
        """
        model_cfg = self.cfg.get_model_config(self.model_type)
        provider = model_cfg.get("provider", "openai")
        model = model_cfg.get("model", "gpt-4o")
        api_base = model_cfg.get("api_base", "https://api.openai.com/v1")
        api_key = self.cfg.get_api_key(provider)

        if not api_key:
            yield "error", f"未设置 {provider} 的API密钥"
            return

        url = f"{api_base.rstrip('/')}/chat/completions"
        headers = self._get_headers(api_key)
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": temperature,
            "max_tokens": 4096,
            "stream": True
        }
        if provider == "deepseek" and thinking:
            payload["thinking"] = {"type": "enabled"}

        debug_request = {
            "url": url, "model": model, "provider": provider,
            "temperature": temperature, "thinking": thinking,
            "system_prompt": system_prompt, "user_prompt": user_prompt,
        }
        collected = []
        error = None
        try:
            resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=120)
            if resp.status_code != 200:
                yield "error", f"HTTP {resp.status_code}: {resp.text[:500]}"
                return
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except Exception:
                    continue  # 坏块跳过，不中断
                choices = obj.get("choices") or []
                if not choices:
                    continue  # usage 块等无 content
                delta = (choices[0].get("delta") or {}).get("content")
                if delta:
                    collected.append(delta)
                    yield "content", delta
        except requests.exceptions.Timeout:
            error = "API请求超时"
        except requests.exceptions.ConnectionError:
            error = "无法连接到API服务器"
        except Exception as e:
            error = str(e)

        if error is not None:
            yield "error", error
            return

        # 调试模式：记录一次完整流式调用（content 已收集完整）
        if is_debug_mode():
            try:
                from .ui.debug_window import log_api_call
                content = "".join(collected)
                response_data = {
                    "status_code": 200,
                    "body_preview": content[:2000],
                    "stream": True,
                    "chunks": len(collected),
                }
                log_api_call(debug_request, response_data, self.model_type, success=True)
            except Exception:
                pass


def call_main(system_prompt, user_prompt, temperature=0.7, thinking=False):
    """调用主力模型"""
    client = APIClient("main")
    return client.call(system_prompt, user_prompt, temperature, thinking=thinking)


def call_lightweight(system_prompt, user_prompt, temperature=0.3, thinking=False):
    """调用轻量模型"""
    client = APIClient("lightweight")
    return client.call(system_prompt, user_prompt, temperature, thinking=thinking)


def call_main_json(system_prompt, user_prompt, temperature=0.7, thinking=False):
    """调用主力模型并解析JSON"""
    client = APIClient("main")
    return client.call_json(system_prompt, user_prompt, temperature, thinking=thinking)


def call_main_stream(system_prompt, user_prompt, temperature=0.7, thinking=False):
    """流式调用主力模型（P1 叙事实时显示用）。
    返回生成器，逐块 yield ("content", delta) / ("error", msg)"""
    client = APIClient("main")
    return client.stream(system_prompt, user_prompt, temperature, thinking=thinking)
