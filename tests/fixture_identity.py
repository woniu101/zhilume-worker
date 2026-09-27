def fixture_config(config):
    for entry in config.get('profiles', [config]):
        entry['identity'] = {'revision':'fixture-v1','quantization':'fp32','artifacts':{k:'revision:fixture-v1-'+k for k in entry.get('models', {'tts':''})}}
    return config
