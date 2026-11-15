"""Compatibility entrypoint for withdrawn VM deployment. No resources are created."""
import json

def main():
 print(json.dumps({"error":"vm_deployment_withdrawn","action":"Use scripts/start-release.ps1 for native startup. No Docker operation was attempted."}),flush=True)
 return 2

if __name__=="__main__":
 raise SystemExit(main())
