from .specification import sign
"""Pinned ComfyUI H3 graphs. References keep explicit roles and frame lengths."""
import hashlib
import json
import math
from pathlib import Path, PurePosixPath

CATALOG = json.loads((Path(__file__).parent / 'contracts/operation-catalog.json').read_text('utf-8'))
MODELS = {m['id']: m for m in CATALOG['videoModels']}


def profiles(config):
    entries = config.get('profiles')
    if not isinstance(entries, list) or not 1 <= len(entries) <= 8:
        raise ValueError('需要 1–8 个视频执行配置')
    result = []
    for entry in entries:
        model = MODELS.get(entry.get('modelId'))
        files = entry.get('models', {})
        if not model or set(files) != {'diffusion', 'clip', 'vae', 'audioVae'}:
            raise ValueError('H3 需要模型、文字编码器、视频和音频 VAE')
        for name in files.values():
            if not isinstance(name, str) or not name or '\\' in name or ':' in name or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts:
                raise ValueError('H3 模型须使用列表中的相对文件名')
        sizes = entry.get('sizes', [[512, 288], [288, 512], [512, 512]])
        frames = entry.get('frames', [124])
        if not isinstance(sizes, list) or not 1 <= len(sizes) <= 16 or any(not isinstance(s, list) or len(s) != 2 or any(type(n) is not int or n < 256 or n > 1344 or n % 32 for n in s) or math.prod(s) > 1344 * 768 for s in sizes):
            raise ValueError('视频尺寸配置无效')
        if not isinstance(frames, list) or not frames or len(frames) > 14 or any(type(n) is not int or not 124 <= n <= 345 or n % 17 != 5 for n in frames):
            raise ValueError('视频帧数应为 124–345 之间的 17k+5')
        refs = entry.get('referenceLimits', {'image': 2, 'video': 1, 'audio': 1}) if model['modes'] == ['reference'] else {'image': 2, 'video': 0, 'audio': 0}
        if set(refs) != {'image', 'video', 'audio'} or any(type(refs[k]) is not int or not 0 <= refs[k] <= n for k, n in [('image', 9), ('video', 3), ('audio', 3)]):
            raise ValueError('视频参考限制无效')
        steps = entry.get('defaultSteps', 20)
        if type(steps) is not int or not 1 <= steps <= 50:
            raise ValueError('视频步数应为 1–50')
        public = dict(modelId=model['id'], workflowRevision=model['workflowRevision'], modes=model['modes'], sizes=sizes,
                      frames=frames, fps=24, referenceLimits=refs, defaultSteps=steps, maxSteps=50, validation='unverified')
        if set(entry.get('identity', {}).get('artifacts', {})) != set(files): raise ValueError('执行规格必须标识所有权重组件')
        sign(public, entry.get('identity'))
        if any(p['public']['profileId'] == public['profileId'] for p in result):
            raise ValueError('视频配置重复')
        result.append({'public': public, 'models': files})
    return result


def validate_environment(info, profile):
    missing = [n for n in MODELS[profile['public']['modelId']]['requiredNodes'] if n not in info]
    if missing:
        raise ValueError('ComfyUI 缺少视频节点：' + ', '.join(missing))
    for node, field, key in [('UNETLoader','unet_name','diffusion'), ('CLIPLoader','clip_name','clip'), ('VAELoader','vae_name','vae'), ('VAELoader','vae_name','audioVae')]:
        if profile['models'][key] not in info[node].get('input', {}).get('required', {}).get(field, [[]])[0]:
            raise ValueError('H3 模型未在加载器列表中找到：' + key)


def validate_input(p, r):
    if any(r.get(k) != p[k] for k in ('modelId', 'profileId', 'workflowRevision')) or r.get('mode') not in p['modes']:
        raise ValueError('视频任务与执行配置不匹配')
    if [r.get('width'), r.get('height')] not in p['sizes'] or r.get('frames') not in p['frames'] or type(r.get('frames')) is not int:
        raise ValueError('视频尺寸或帧数不受配置支持')
    if not isinstance(r.get('prompt'), str) or not r['prompt'].strip() or len(r['prompt']) > 20000 or type(r.get('seed')) is not int or not 0 <= r['seed'] <= 9007199254740991 or type(r.get('steps')) is not int or not 1 <= r['steps'] <= p['maxSteps'] or type(r.get('includeAudio')) is not bool:
        raise ValueError('视频提示词或参数无效')
    refs = r.get('references')
    if not isinstance(refs, list) or len(refs) > 12 or any(not isinstance(v, dict) for v in refs):
        raise ValueError('视频参考最多 12 个')
    roles = [v.get('role') for v in refs]
    expected = {'text': [], 'first': ['first'], 'last': ['last'], 'first-last': ['first','last']}
    if r['mode'] != 'reference':
        if roles != expected[r['mode']]: raise ValueError('首尾帧角色不完整')
    elif not refs or any(role not in ('image','video','audio') for role in roles):
        raise ValueError('请选择图片、视频或音频参考')
    for kind in ('image','video','audio'):
        if roles.count(kind) > p['referenceLimits'][kind]: raise ValueError('参考数量超过执行配置')
    totals = {'video': 0, 'audio': 0}
    for ref in refs:
        if not isinstance(ref.get('assetId'), str) or not ref['assetId']: raise ValueError('参考素材不存在')
        if ref['role'] in totals:
            start, frames = ref.get('start'), ref.get('frames')
            if type(start) not in (int, float) or not math.isfinite(start) or not 0 <= start <= 86400 or type(frames) is not int or frames < 56 or frames > min(r['frames'], 345) or frames % 17 != 5:
                raise ValueError('参考片段需为 17k+5 帧，至少 56 帧且不超过输出长度')
            totals[ref['role']] += frames / 24
    if any(n > 15 for n in totals.values()): raise ValueError('视频/音频参考总时长各不超过 15 秒')
    if list(dict.fromkeys(v['assetId'] for v in refs)) != r.get('referenceAssetIds'):
        raise ValueError('参考素材顺序不一致')


def build_graph(profile, operation, request, uploaded, prefix):
    p = profile['public']; validate_input(p, request)
    if operation != 'video.generate.v1' or len(uploaded) != len(request['references']): raise ValueError('视频输入不完整')
    graph = {}
    def node(key, kind, **inputs):
        graph[key] = {'class_type': kind, 'inputs': inputs}; return [key, 0]
    f = profile['models']
    model = node('unet','UNETLoader',unet_name=f['diffusion'],weight_dtype='default')
    clip = node('clip','CLIPLoader',clip_name=f['clip'],type='minimax',device='default')
    vae = node('vae','VAELoader',vae_name=f['vae'])
    audio_vae = node('audio_vae','VAELoader',vae_name=f['audioVae'])
    args = dict(clip=clip,vae=vae,prompt=request['prompt'],width=request['width'],height=request['height'],length=request['frames'])
    counts = {'image': 0, 'video': 0, 'audio': 0}
    for i, (ref, name) in enumerate(zip(request['references'], uploaded, strict=True)):
        key = 'reference_' + str(i); role = ref['role']
        if role in ('first','last','image'):
            value = node(key,'LoadImage',image=name)
        elif role == 'video':
            value = node(key,'GetVideoComponents',video=node(key+'_file','LoadVideo',file=name))
        else:
            value = node(key,'LoadAudio',audio=name)
        if role in ('first','last'): args[role+'_frame'] = value
        else:
            counts[role] += 1
            args[f'ref_{role}s.ref_{role}_{counts[role]}'] = value
    reference = request['mode'] == 'reference'
    if reference: args.update(audio_vae=audio_vae, ref_image_size='match')
    positive = node('condition','MiniMaxH3ReferenceToVideo' if reference else 'MiniMaxH3ImageToVideo',**args)
    noise = node('noise','RandomNoise',noise_seed=request['seed'])
    guider = node('guider','BasicGuider',model=model,conditioning=positive)
    sampler = node('sampler','KSamplerSelect',sampler_name='res_multistep')
    sigmas = node('sigmas','BasicScheduler',model=model,scheduler='simple',steps=request['steps'],denoise=1.0)
    samples = node('samples','SamplerCustomAdvanced',noise=noise,guider=guider,sampler=sampler,sigmas=sigmas,latent_image=['condition',1])
    pixels = node('decode','VAEDecode',samples=samples,vae=vae)
    audio = node('decode_audio','VAEDecodeAudio',samples=samples,vae=audio_vae)
    video = node('video','CreateVideo',images=pixels,fps=24.0,**({'audio': audio} if request['includeAudio'] else {}))
    node('output','SaveVideo',video=video,filename_prefix=prefix,format='mp4',**{'format.codec':'h264'})
    return graph
