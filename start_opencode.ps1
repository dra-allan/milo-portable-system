$env:GEMINI_API_KEY = 'AIzaSyA4VXSMxV58TrISLJvILFgl1deugPsIvRc'
$env:PATH = 'C:\Users\Administrator\AppData\Roaming\npm;C:\Program Files\nodejs;C:\Program Files\Git\cmd;' + $env:PATH
& 'C:\Users\Administrator\AppData\Roaming\npm\opencode.cmd' serve --port 4096 *>> 'C:\milo-portable-system\opencode_serve.log'
