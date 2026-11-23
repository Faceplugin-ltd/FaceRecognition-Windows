# Runtime libraries

Download the Windows runtime from the GitHub release into `cpu\`:

https://github.com/Faceplugin-ltd/FaceRecognition-Windows/releases/tag/latest

The release holds two editions — **Lite** and **Plus**. Pick one, and put every file
from the archive **directly** in `cpu\`, not in a nested folder.

Expected files (CPU only; there is no `gpu\` package):

- `FaceRecognitionSDK.dll`
- `facerec.dll`
- `onnxruntime.dll`, `onnxruntime_providers_shared.dll`
- `far.fpk`
- the OpenCV, OpenSSL and VC++ runtime DLLs that ship in the archive

Then `run.bat`. It puts `lib\cpu` on `PATH` for you.
