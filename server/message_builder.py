"""构建带文件附件的用户消息（含多模态 vision）。"""

import base64
from pathlib import Path

from agent.config_loader import get_config
from agent.file_manager import get_file_manager, get_image_mime, is_image_file


def build_message_with_files(text: str, file_ids: list[str]) -> str | list:
    """构建用户消息，图片以多模态格式发送给模型。

    - vision 开启 + 图片文件：读取内容转 base64，以 image_url 格式发送给模型
    - vision 关闭 + 图片文件：仅传文件路径文本，模型无法直接看到图片
    - 非图片文件：始终保持原有文本路径格式
    - 无图片时返回纯文本字符串；有图片且 vision 开启时返回多模态 content 列表
    """
    if not file_ids:
        return text
    try:
        fm = get_file_manager()
    except RuntimeError:
        return text

    try:
        cfg = get_config()
        vision_enabled = cfg.api.vision
    except Exception:
        vision_enabled = False

    image_parts: list[dict] = []
    file_lines: list[str] = []

    for fid in file_ids:
        info = fm.get_file(fid)
        if not info:
            continue
        filename = info["filename"]
        stored_path = Path(info["stored_path"])

        if is_image_file(filename) and stored_path.exists():
            if vision_enabled:
                try:
                    with open(stored_path, "rb") as f:
                        img_data = base64.b64encode(f.read()).decode("utf-8")
                    mime = get_image_mime(filename)
                    image_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{img_data}"},
                    })
                    file_lines.append(f"- {filename} (图片，已直接传给模型)")
                except Exception:
                    file_lines.append(
                        f"- {filename} (路径: {info['stored_path']}, 大小: {info['size']} 字节)"
                    )
            else:
                file_lines.append(
                    f"- {filename} (图片，当前模型不支持视觉能力，无法直接查看图片内容。"
                    f"路径: {info['stored_path']}, 大小: {info['size']} 字节)"
                )
        else:
            file_lines.append(
                f"- {filename} (路径: {info['stored_path']}, 大小: {info['size']} 字节)"
            )

    if not file_lines and not image_parts:
        return text

    if not image_parts:
        return text + "\n\n[用户上传的文件]\n" + "\n".join(file_lines)

    content_parts: list[dict] = []
    full_text = text
    if file_lines:
        full_text += "\n\n[用户上传的文件]\n" + "\n".join(file_lines)
    content_parts.append({"type": "text", "text": full_text})
    content_parts.extend(image_parts)
    return content_parts
