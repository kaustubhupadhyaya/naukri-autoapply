"""Act on a decided Answer. Every filler only ACTS — engine.py re-probes and verifies.

Click escalation (native -> JS -> ActionChains) tries the next channel only when the caller's
own re-probe shows the previous one didn't register; fillers themselves are honest about which
channel they used ("ok" here means "the browser accepted the action", not "it verified").
v1's `_click_radio_option` returned True unconditionally after a JS click — that's the bug this
module exists to not repeat.
"""
from dataclasses import dataclass

from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import Select


@dataclass
class FillResult:
    ok: bool
    channel: str = ""
    note: str = ""


def _click_ladder(driver, els):
    """Try each element in turn (e.g. [label_ref, input_ref]) with native -> JS -> ActionChains."""
    last_err = None
    for el in els:
        if el is None:
            continue
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        except Exception:
            pass
        try:
            el.click()
            return FillResult(True, "native")
        except Exception as e:
            last_err = e
        try:
            driver.execute_script("arguments[0].click();", el)
            return FillResult(True, "js")
        except Exception as e:
            last_err = e
        try:
            ActionChains(driver).move_to_element(el).click().perform()
            return FillResult(True, "action_chains")
        except Exception as e:
            last_err = e
    return FillResult(False, "", f"all click channels failed: {last_err}")


def _set_text(driver, el, value, is_input):
    if is_input:
        try:
            el.clear()
        except Exception:
            pass
        try:
            el.send_keys(value)
            driver.execute_script(
                "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));"
                "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));", el)
            return FillResult(True, "send_keys")
        except Exception:
            pass
        try:
            driver.execute_script(
                "var e=arguments[0],v=arguments[1];"
                "var proto = e.tagName==='TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;"
                "var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;"
                "setter.call(e, v);"
                "e.dispatchEvent(new Event('input',{bubbles:true}));"
                "e.dispatchEvent(new Event('change',{bubbles:true}));", el, value)
            return FillResult(True, "js_value_setter")
        except Exception as e:
            return FillResult(False, "", str(e))
    else:  # contenteditable composer
        try:
            driver.execute_script(
                "var el=arguments[0],v=arguments[1];el.focus();"
                "document.execCommand('selectAll',false,null);"
                "document.execCommand('insertText',false,v);"
                "el.dispatchEvent(new Event('input',{bubbles:true}));", el, value)
            return FillResult(True, "exec_command")
        except Exception:
            pass
        try:
            el.click()
            el.send_keys(value)
            return FillResult(True, "send_keys")
        except Exception as e:
            return FillResult(False, "", str(e))


def fill_radio_or_checkbox(driver, snap, widget, answer):
    wanted = answer.values if (widget.get("kind") == "checkbox" and answer.values) else [answer.value]
    results = []
    for val in wanted:
        target = next((o for o in widget.get("options", [])
                       if (o.get("text") or "").strip().lower() == val.strip().lower()), None)
        if target is None:  # loose fallback
            target = next((o for o in widget.get("options", [])
                           if val.strip().lower() in (o.get("text") or "").strip().lower()), None)
        if target is None:
            results.append(FillResult(False, "", f"option '{val}' not found among "
                                                 f"{[o.get('text') for o in widget.get('options', [])]}"))
            continue
        if target.get("checked"):  # a retry after a partial multi-select: keep what already registered
            results.append(FillResult(True, "already"))
            continue
        results.append(_click_ladder(driver, [snap.el(target.get("ref")), snap.el(target.get("inputRef"))]))
    ok = bool(results) and all(r.ok for r in results)
    return FillResult(ok, "/".join(r.channel for r in results if r.channel),
                      "; ".join(r.note for r in results if r.note))


def fill_select(driver, snap, widget, answer):
    el = snap.el(widget.get("ref"))
    if el is None:
        return FillResult(False, "", "select element not found")
    try:
        Select(el).select_by_visible_text(answer.value)
        return FillResult(True, "select_by_text")
    except Exception:
        pass
    try:
        opts = [o.get("text", "") for o in widget.get("options", [])]
        match = next((o for o in opts if o.strip().lower() == answer.value.strip().lower()), None)
        if match:
            Select(el).select_by_visible_text(match)
            return FillResult(True, "select_by_text_ci")
    except Exception as e:
        return FillResult(False, "", str(e))
    return FillResult(False, "", f"'{answer.value}' not in select options")


def fill_text(driver, snap, widget, answer):
    el = snap.el(widget.get("ref"))
    if el is None:
        return FillResult(False, "", "text control not found")
    is_input = widget.get("kind") in ("text", "textarea", "date", "date_text")
    return _set_text(driver, el, answer.value, is_input)


def fill_date_split(driver, snap, widget, answer):
    if not answer.parts:
        return FillResult(False, "", "no date parts decided")
    results = []
    for part in widget.get("parts", []):
        role = part.get("role")
        value = answer.parts.get(role)
        if value is None:
            continue
        el = snap.el(part.get("ref"))
        if el is None:
            results.append(FillResult(False, "", f"{role} control not found"))
            continue
        if part.get("options") is not None:
            try:
                Select(el).select_by_visible_text(value)
                results.append(FillResult(True, "select_by_text"))
                continue
            except Exception:
                opts = [o.get("text", "") for o in part.get("options", [])]
                match = next((o for o in opts if o.strip().lstrip("0") == value.strip().lstrip("0")), None)
                if match:
                    try:
                        Select(el).select_by_visible_text(match)
                        results.append(FillResult(True, "select_by_text_ci"))
                        continue
                    except Exception as e:
                        results.append(FillResult(False, "", str(e)))
                        continue
                results.append(FillResult(False, "", f"'{value}' not in {role} options"))
                continue
        results.append(_set_text(driver, el, value, True))
    ok = bool(results) and all(r.ok for r in results)
    return FillResult(ok, "/".join(r.channel for r in results if r.channel),
                       "; ".join(r.note for r in results if r.note))


def fill_chips(driver, snap, widget, answer):
    target = next((o for o in widget.get("options", [])
                   if (o.get("text") or "").strip().lower() == answer.value.strip().lower()), None)
    if target is None:
        target = next((o for o in widget.get("options", [])
                       if answer.value.strip().lower() in (o.get("text") or "").strip().lower()), None)
    if target is None:
        return FillResult(False, "", f"chip '{answer.value}' not found")
    return _click_ladder(driver, [snap.el(target.get("ref"))])


def fill_file(driver, snap, widget, resume_path):
    el = snap.el(widget.get("ref"))
    if el is None:
        return FillResult(False, "", "file input not found")
    try:
        driver.execute_script(
            "arguments[0].style.display='block';arguments[0].style.visibility='visible';"
            "arguments[0].style.opacity='1';arguments[0].removeAttribute('hidden');"
            "arguments[0].removeAttribute('disabled');", el)
    except Exception:
        pass
    try:
        el.send_keys(resume_path)
        return FillResult(True, "send_keys")
    except Exception as e:
        return FillResult(False, "", str(e))


FILLERS = {
    "radio": fill_radio_or_checkbox,
    "checkbox": fill_radio_or_checkbox,
    "select": fill_select,
    "text": fill_text,
    "textarea": fill_text,
    "composer": fill_text,
    "date": fill_text,
    "date_text": fill_text,
    "date_split": fill_date_split,
    "chips": fill_chips,
}


def fill(driver, snap, widget, answer):
    fn = FILLERS.get(widget.get("kind"))
    if fn is None:
        return FillResult(False, "", f"no filler for kind '{widget.get('kind')}'")
    return fn(driver, snap, widget, answer)
