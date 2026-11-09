let timezone='Europe/Moscow';
export function setDisplayTimezone(value:string){try{new Intl.DateTimeFormat('ru-RU',{timeZone:value}).format();timezone=value;}catch{timezone='Europe/Moscow';}}
export const systemDateOptions=():Intl.DateTimeFormatOptions=>({timeZone:timezone});
export const displayTimezone=()=>timezone;
