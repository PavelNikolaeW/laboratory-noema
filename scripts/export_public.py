"""Run from the project root. Bundle only these outputs for the participant."""
import json
import sys
import zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lab.app import app
from lab import dto

root=Path(__file__).resolve().parents[1]
public=root/'public';public.mkdir(exist_ok=True)
(public/'openapi.json').write_text(json.dumps(app.openapi(),ensure_ascii=False,indent=2))
models=['Operation','Recipe','Note','Prediction','Submission','PublicSample','Observation','PublicOperation','EventPage','ErrorResponse','PublicBatch','PublicOrder','PublicStandard','PublicSubmission','SavedNote','SavedRecipe','SavedPrediction','Session','PublicInstrument']
(public/'schemas.json').write_text(json.dumps({name:getattr(dto,name).model_json_schema() for name in models},ensure_ascii=False,indent=2))
with zipfile.ZipFile(public/'participant.zip','w',zipfile.ZIP_DEFLATED) as archive:
    for path in [root/'PUBLIC_API.md',root/'pyproject.toml',*sorted((root/'sdk').glob('*.py')),public/'openapi.json',public/'schemas.json']:
        archive.write(path,path.relative_to(root))
print('Public package written to public/participant.zip')
