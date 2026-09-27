"""Portable execution identity. Deployment paths never participate in the digest."""
import hashlib
import json
import re


def sign(public, identity):
    if not isinstance(identity, dict) or not identity.get('revision') or not identity.get('quantization') or not isinstance(identity.get('artifacts'), dict) or not identity['artifacts']:
        raise ValueError('请配置 identity：权重 revision、quantization 和 artifacts 不可变标识')
    if 'REPLACE' in json.dumps(identity) or identity['quantization'].startswith('declare-'): raise ValueError('请将 identity 示例占位符替换为真实的固定权重版本和量化')
    if any(not isinstance(v, str) or not re.fullmatch(r'(sha256:[a-f0-9]{64}|revision:[A-Za-z0-9_.@-]{3,160})', v) for v in identity['artifacts'].values()):
        raise ValueError('组件标识应为 sha256 摘要或已固定的 revision 标识，不得使用本机路径')
    public['identity'] = identity
    public['profileId'] = hashlib.sha256(json.dumps(public, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return public
