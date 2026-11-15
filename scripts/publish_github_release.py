"""Legacy publisher is disabled. Publication requires a reviewed model-free inventory."""
import json

def main():
 print(json.dumps({"error":"legacy_publisher_disabled","action":"Use an explicitly authorized release workflow with a reviewed model-free inventory, private-state exclusions and GitHub digest readback. This legacy command performs no Git, credential or network operations."}),flush=True)
 return 2

if __name__=="__main__":
 raise SystemExit(main())
