"""Audience-first release guidance from the actual verified asset inventory."""
import re
from urllib.parse import quote

def asset_catalog(assets):
    result={}
    for row in assets:
        name=row['name']
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+',name) or name in result:raise ValueError('invalid_release_asset')
        if not isinstance(row['bytes'],int) or row['bytes']<0 or not re.fullmatch(r'[0-9a-f]{64}',row['sha256']):raise ValueError('invalid_release_asset')
        result[name]=row
    return result

def choices(repo,version,assets):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo) or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?',version):raise ValueError('invalid_release_identity')
    rows=asset_catalog(assets);prefix='PSLC-Agent-'+version;lite=prefix+'-Lite.zip';source=prefix+'-source.zip'
    parts=sorted(n for n in rows if re.fullmatch(re.escape(prefix)+r'-Full\.zip\.part\d{2}',n))
    if len(parts)<2 or parts!=[prefix+'-Full.zip.part'+str(i+1).zfill(2) for i in range(len(parts))]:raise ValueError('incomplete_full_parts')
    full=[*parts,prefix+'-Full.parts.json','assemble_full.py']
    if any(n not in rows for n in [lite,source,*full]):raise ValueError('missing_primary_download')
    base='https://github.com/'+repo+'/releases/download/v'+version+'/'
    def link(name,label):return '['+label+']('+base+quote(name,safe='')+')'
    full_links=' · '.join(link(n,'Часть'+str(i+1)) for i,n in enumerate(parts))+' · '+link(full[-2],'Манифест Full')+' · '+link(full[-1],'Сборщик')
    text='## Что скачать\n\nВыберите один вариант. Для первого запуска рекомендуем **Lite**.\n\n'
    text+='**Перед установкой:** Python3.12+ x64; для локальной модели —32ГБ RAM. Видеокарта не обязательна. Скачивайте только опубликованные и проверенные поставки.\n\n'
    text+='| Вариант | Назначение | Скачать |\n|---|---|---|\n'
    text+='| **Lite — '+str(round(rows[lite]['bytes']/1_000_000))+'МБ** | Программа с CPU runtime. Модель загружается кнопкой «Настройки → Модели». | '+link(lite,'Скачать Lite.zip')+' |\n'
    text+='| **Full — модель включена** | Та же программа с готовой Qwen4B. Файлов: '+str(len(full))+'. | '+full_links+' |\n'
    text+='| **Исходники — '+str(round(rows[source]['bytes']/1_000_000))+'МБ** | Для разработчиков; готовый web и полные входы пересборки. | '+link(source,'Скачать Source.zip')+' |\n'
    text+='\n### Установка\n\n1. Сначала установите Python3.12+ x64.\n2. Lite: распакуйте один ZIP. Full: скачайте все файлы строки Full в одну папку и выполните сборку ниже.\n3. В распакованной программе откройте START_HERE.md и выполните install-release.ps1, затем start-release.ps1.\n4. Откройте http://127.0.0.1:8787/. В новой пустой установке — admin/admin; существующие учётные записи не сбрасываются.\n\n'
    text+='Full, Windows PowerShell, из папки скачивания:\n```powershell\npy -3.12 .\\assemble_full.py .\\'+prefix+'-Full.parts.json\n```\n'
    text+='Full, Linux: `python3 assemble_full.py '+prefix+'-Full.parts.json`.\n\n'
    text+='Если launcher `py` отсутствует, используйте полный путь к установленному Python. Full включает веса модели; Python-зависимости устанавливаются из package index либо подготовленного wheelhouse.\n'
    return text

def render(repo,version,assets,notes=''):
    rows=asset_catalog(assets);text='# PSLC Agent '+version+'\n\n'+choices(repo,version,assets)
    if notes.strip():text+='\n<details>\n<summary>Изменения выпуска и проверки</summary>\n\n'+notes.strip()+'\n\n</details>\n'
    text+='\n<details>\n<summary>Контрольные суммы, служебные файлы и автоматические архивы GitHub</summary>\n\n'
    text+='Список Assets содержит варианты для разных задач. .sha256, манифесты и DOWNLOADS.md нужны для проверки и технической передачи. Автоматические GitHub «Source code (zip/tar.gz)» — снимки Git без compiled web/runtime; подготовленный комплект разработчика — Source.zip из таблицы выше.\n\n'
    base='https://github.com/'+repo+'/releases/download/v'+version+'/'
    text+='| Файл | Байты | SHA256 |\n|---|---:|---|\n'
    for name,row in rows.items():text+='| ['+name+']('+base+quote(name,safe='')+') | '+str(row['bytes'])+' | `'+row['sha256']+'` |\n'
    return text+'\nSHA256 проверяет целостность, а не корпоративную сертификацию. Полная инструкция и границы приёмки — в документации.\n\n</details>\n'
