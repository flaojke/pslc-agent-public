import sys
if __name__=='__main__':
    if sys.argv[1:]==['--service']:
        from agent.windows_service import dispatch
        dispatch()
    else:
        from agent.__main__ import main
        main()
