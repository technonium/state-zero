import os
import sys
import json
import argparse
from pathlib import Path

# Add src/scripts to path to import utils
sys.path.insert(0, str(Path(__file__).parent))
from utils import get_project_root, get_output_root

# Load .env from project root
from dotenv import load_dotenv
load_dotenv(dotenv_path=get_project_root() / '.env')

from google_image_client import GoogleImageClient

class ImageGenerator:
    def __init__(self):
        self.mock_mode = False
        try:
            self.client = GoogleImageClient()
        except Exception as e:
            self.mock_mode = True
            self._init_error = str(e)

    def generate(self, prompt_json: dict, output_path: str):
        """Generate image from JSON prompt"""
        if self.mock_mode:
            print(f"⚠️ Falling back to mock image generation: {self._init_error}")
            try:
                from PIL import Image
                img = Image.new('RGB', (1080, 1920), color='blue')
                img.save(output_path)
            except ImportError:
                print("PIL missing, using fallback mock")
                with open(output_path, 'wb') as f:
                    f.write(b"")
            print(f"✅ Mock image saved to {output_path}")
            return

        self.client.generate_from_json(prompt_json, Path(output_path))

def main():
    parser = argparse.ArgumentParser()
    output_root = get_output_root()
    parser.add_argument('--json', help='Path to image_prompt.json', default=str(output_root / 'image_prompt.json'))
    parser.add_argument('--out', help='Output image path', default=str(output_root / 'generated_art.png'))
    args = parser.parse_args()

    generator = ImageGenerator()

    json_path = Path(args.json).expanduser()
    out_path = Path(args.out).expanduser()

    if not json_path.exists():
        print(f"❌ Could not find {json_path}")
        sys.exit(1)

    with open(json_path, 'r') as f:
        prompt_json = json.load(f)

    generator.generate(prompt_json, str(out_path))

if __name__ == '__main__':
    main()
