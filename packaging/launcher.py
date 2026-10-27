"""PyInstaller entry point for JobQuestAI.exe."""
import sys

from jobhunt.desktop import main

if __name__ == "__main__":
    sys.exit(main())
