'use strict';
const fs=require('node:fs/promises');
const path=require('node:path');

// Only return configuration paths from the selected process. Never expose its
// complete environment, which may contain API keys and other credentials.
async function processPaths(pid,{procRoot='/proc'}={}) {
  let handle;
  try {
    handle=await fs.open(path.join(procRoot,String(pid),'environ'),'r');
    const buffer=Buffer.alloc(256*1024+1);
    const {bytesRead}=await handle.read(buffer,0,buffer.length,0);
    if(bytesRead>256*1024)return {};
    const result={};
    for(const field of buffer.subarray(0,bytesRead).toString().split('\0')) {
      const at=field.indexOf('=');
      const key=field.slice(0,at),value=field.slice(at+1);
      if(['HOME','CODEX_HOME','CLAUDE_CONFIG_DIR'].includes(key)&&path.isAbsolute(value))result[key]=value;
    }
    return result;
  }catch{return {};}
  finally{await handle?.close();}
}
module.exports={processPaths};
