"""Native SCM host. Installation is an explicit selected-target operation."""
import os
from pathlib import Path
from threading import Event,Thread
import servicemanager,win32service,win32serviceutil
from agent.runtime import load_config
from agent.__main__ import run

class AgentService(win32serviceutil.ServiceFramework):
    _svc_name_='PSLCAgent'
    _svc_display_name_='PSLC Agent'
    _svc_description_='PSLC node executor; explicit capabilities and verified plans.'
    def __init__(self,args):
        super().__init__(args);self.stop_event=Event();self.finished=Event()
    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING,waitHint=30000)
        self.stop_event.set()
        def progress():
            while not self.finished.wait(5):self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING,waitHint=30000)
        Thread(target=progress,daemon=True).start()
    def SvcDoRun(self):
        try:
            config_path=Path(os.environ.get('PROGRAMDATA',r'C:\ProgramData'))/'PSLC-Agent/agent-config.json'
            config=load_config(config_path)
            run(config.server,config.state,config.commands_enabled,False,False,config.installations_enabled,config.diagnostics_enabled,config.ca_file,self.stop_event,config_path=str(config_path))
        except Exception:raise RuntimeError('service_configuration_or_execution_failed') from None
        finally:self.finished.set()

def dispatch():
    servicemanager.Initialize();servicemanager.PrepareToHostSingle(AgentService);servicemanager.StartServiceCtrlDispatcher()
