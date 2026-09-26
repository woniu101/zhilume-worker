"""Runs only inside the explicitly configured upstream IndexTTS environment."""
import json
import os
import sys
from pathlib import Path


def infer_kwargs(source, clips):
    values = dict(spk_audio_prompt=clips['speaker'], text=source['text'], lang=source['language'],
                  duration_factor=1 / source['speed'], use_random=False, verbose=False)
    mode = source['emotionMode']
    if mode != 'follow':
        values['emo_alpha'] = source['emotionAlpha']
    if mode == 'reference':
        values['emo_audio_prompt'] = clips['emotionReference']
    elif mode == 'vector':
        values['emo_vector'] = source['emotionVector']
    elif mode == 'text':
        values.update(use_emo_text=True, emo_text=source['emotionText'])
    return values


def main():
    request = json.loads(Path(sys.argv[1]).read_text('utf-8'))
    config = request['config']
    if sys.platform == 'linux':
        # Prevent orphaned CUDA work if the owning Worker is terminated unexpectedly.
        import ctypes
        import signal
        if ctypes.CDLL(None).prctl(1, signal.SIGKILL) != 0 or os.getppid() != request['parentPid']:
            raise RuntimeError('Worker process ownership lost')
    sys.path.insert(0, config['repository'])
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    from indextts.utils import model_download
    def unavailable(*args, **kwargs):
        raise RuntimeError('Auxiliary model missing; automatic downloads disabled')
    model_download.ensure_models_available = unavailable
    from indextts.infer_v2_5 import IndexTTS2
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('GPU execution requires CUDA; refusing CPU fallback')
    model = IndexTTS2(cfg_path=str(Path(config['modelDirectory']) / 'config.yaml'), model_dir=config['modelDirectory'],
                      use_bf16=True, device='cuda:0', use_cuda_kernel=False, use_deepspeed=False, use_qwen_emo=config.get('enableEmotionText', False))
    raw = str(Path(request['output']).with_name('raw-speech.wav'))
    model.infer(**infer_kwargs(request['input'], request['clips']), output_path=raw)
    import subprocess
    subprocess.run([config['ffmpeg'], '-nostdin', '-v', 'error', '-y', '-i', raw, '-ac', '1', '-ar', '24000', '-c:a', 'pcm_s16le', request['output']], check=True, timeout=60)


if __name__ == '__main__':
    main()
