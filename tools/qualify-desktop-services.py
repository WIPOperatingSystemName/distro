#!/usr/bin/env python3
"""Exercise real target service foundations and GIO IPC on a private Unix bus."""
from pathlib import Path
import hashlib,json,re,select,shlex,shutil,subprocess,sys,tempfile
project=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(project/'src'))
from distro_build.model import catalog
from distro_build.graph import plan
from distro_build.packaging import PacmanToolkit
from distro_build.desktop_native import _link_wrapper
selected=['glib','lua','duktape','json-glib','libgudev','gstreamer','gst-plugins-base','gst-plugins-good','dbus']
records=[json.loads((project/'out/state/packages'/f'{r.name}.json').read_text()) for r in plan(catalog(project),selected)]
digest=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
identity=hashlib.sha256(json.dumps({'packages':{r['package']:r['sha256'] for r in records},'qualifier':digest(__file__)},sort_keys=True).encode()).hexdigest()
work=project/'out/qualification/desktop-services'/identity;work.mkdir(parents=True,exist_ok=True);root=work/'sysroot'
if root.exists():shutil.rmtree(root)
PacmanToolkit(project/'out/native-toolkit/prefix').install(root,[Path(r['path']) for r in records],bootstrap=True,expected_hashes={Path(r['path']).name:r['sha256'] for r in records})
bootstrap=project/'out/bootstrap/root';shutil.copytree(bootstrap/'usr/include',root/'usr/include',symlinks=True,dirs_exist_ok=True);tools=bootstrap/'tools'
compiler=tools/'pass2/bin/x86_64-custom-linux-gnu-gcc';wrapper=work/'target-cc';_link_wrapper(wrapper,compiler,root,[root,tools,work])
env={'PATH':'/usr/bin:/bin','LC_ALL':'C','LANG':'C','TZ':'UTC','PKG_CONFIG_SYSROOT_DIR':str(root),'PKG_CONFIG_PATH':'','PKG_CONFIG_LIBDIR':str(root/'usr/lib/pkgconfig')+':'+str(root/'usr/share/pkgconfig'),'GST_PLUGIN_SYSTEM_PATH_1_0':str(root/'usr/lib/gstreamer-1.0'),'GST_PLUGIN_PATH_1_0':'','GST_REGISTRY_1_0':str(work/'gst-registry.bin'),'GST_REGISTRY_FORK':'no','GIO_MODULE_DIR':str(root/'usr/lib/gio/modules'),'GIO_USE_VFS':'local','GSETTINGS_BACKEND':'memory'}
source=work/'probe.c'
source.write_text(r'''
#define _GNU_SOURCE
#include <glib.h>
#include <gio/gio.h>
#include <json-glib/json-glib.h>
#include <gudev/gudev.h>
#include <lua.h>
#include <lualib.h>
#include <lauxlib.h>
#include <duktape.h>
#include <gst/gst.h>
#include <link.h>
#include <stdio.h>
#include <stdlib.h>
static int paths(struct dl_phdr_info *i,size_t n,void *p){(void)n;(void)p;if(i->dlpi_name[0])printf("LOADED %s\n",i->dlpi_name);return 0;}
int main(void){
 GError *error=NULL;GRegex *r=g_regex_new("^[0-9]+$",0,0,&error);if(!r||!g_regex_match(r,"42",0,NULL))return 2;g_regex_unref(r);
 JsonParser *jp=json_parser_new();if(!json_parser_load_from_data(jp,"{\"answer\":42}",-1,&error))return 3;if(json_object_get_int_member(json_node_get_object(json_parser_get_root(jp)),"answer")!=42)return 4;g_object_unref(jp);
 if(!g_udev_client_get_type())return 5;
 lua_State *l=luaL_newstate();if(!l)return 6;luaL_openlibs(l);if(luaL_dostring(l,"return 6*7")||lua_tointeger(l,-1)!=42)return 7;lua_close(l);
 duk_context *d=duk_create_heap_default();if(!d||duk_peval_string(d,"6*7")||duk_get_int(d,-1)!=42)return 8;duk_destroy_heap(d);
 gst_init(NULL,NULL);GstElementFactory *wf=gst_element_factory_find("wavparse");if(!wf)return 9;GstElement *we=gst_element_factory_create(wf,NULL);if(!we)return 9;gst_object_unref(wf);
 GDBusConnection *bus=g_dbus_connection_new_for_address_sync(getenv("CUSTOM_BUS_ADDRESS"),G_DBUS_CONNECTION_FLAGS_AUTHENTICATION_CLIENT|G_DBUS_CONNECTION_FLAGS_MESSAGE_BUS_CONNECTION,NULL,NULL,&error);
 if(!bus){fprintf(stderr,"GIO bus connection: %s\n",error->message);return 10;}
 GVariant *reply=g_dbus_connection_call_sync(bus,"org.freedesktop.DBus","/org/freedesktop/DBus","org.freedesktop.DBus","GetId",NULL,G_VARIANT_TYPE("(s)"),G_DBUS_CALL_FLAGS_NONE,3000,NULL,&error);
 if(!reply){fprintf(stderr,"GIO GetId: %s\n",error->message);return 11;}g_variant_unref(reply);g_dbus_connection_close_sync(bus,NULL,NULL);g_object_unref(bus);
 dl_iterate_phdr(paths,NULL);gst_object_unref(we);puts("CUSTOM_DESKTOP_SERVICES_FOUNDATIONS_OK");return 0;
}
''')
modules=['glib-2.0','gio-2.0','json-glib-1.0','gudev-1.0','lua5.4','duktape','gstreamer-1.0']
cflags=shlex.split(subprocess.check_output(['pkg-config','--cflags',*modules],env=env,text=True));libs=shlex.split(subprocess.check_output(['pkg-config','--libs',*modules],env=env,text=True))
obj=work/'probe.o';binary=work/'probe';subprocess.run([str(wrapper),'-c',str(source),'-o',str(obj),*cflags],env=env,check=True)
compiled=subprocess.run([str(wrapper),'-static-libgcc','-Wl,--verbose',str(obj),*libs,'-ldl','-o',str(binary)],env=env,capture_output=True,text=True,check=True);(work/'link.log').write_text(compiled.stdout+compiled.stderr)
for value in re.findall(r'attempt to open (.+) succeeded',compiled.stdout):
 path=Path(value)
 if path.is_absolute() and not any(path.resolve().is_relative_to(p.resolve()) for p in (root,tools,work)):raise RuntimeError('Outside target linker input: '+value)
loader=root/'usr/lib/ld-linux-x86-64.so.2'
def command(binary):
 prefix=[str(loader),'--inhibit-cache','--library-path',str(root/'usr/lib')];listing=subprocess.check_output([*prefix,'--list',str(binary)],env=env,text=True)
 for line in listing.splitlines():
  if 'linux-vdso' in line:continue
  match=re.search(r'(?:=>\s*)?(/[^\s]+)\s+\(0x[0-9a-f]+\)',line)
  if not match or not Path(match[1]).resolve().is_relative_to(root):raise RuntimeError('Outside target loader resolution: '+line)
 (work/(binary.name+'-loader-list.txt')).write_text(listing);return [*prefix,str(binary)]
socket_dir=tempfile.TemporaryDirectory(prefix='custom-desktop-bus-',dir='/tmp')
config=work/'bus.conf';config.write_text('<busconfig><type>session</type><listen>unix:path='+str(Path(socket_dir.name)/'bus')+'</listen><auth>EXTERNAL</auth><policy context="default"><allow send_destination="*"/><allow receive_sender="*"/><allow own="*"/></policy></busconfig>')
with (work/'bus.log').open('w') as buslog:
 daemon=subprocess.Popen([*command(root/'usr/bin/dbus-daemon'),'--nofork','--config-file='+str(config),'--print-address=1'],env=env,stdout=subprocess.PIPE,stderr=buslog,text=True)
 try:
  if not select.select([daemon.stdout],[],[],5)[0]:raise RuntimeError('Private source-built bus did not start')
  address=daemon.stdout.readline().strip();env['CUSTOM_BUS_ADDRESS']=address
  result=subprocess.run(command(binary),env=env,capture_output=True,text=True,check=False,timeout=15)
  (work/'probe.log').write_text(result.stdout+result.stderr)
  result.check_returncode()
 finally:
  daemon.terminate()
  try:daemon.wait(timeout=5)
  except subprocess.TimeoutExpired:daemon.kill();daemon.wait()
  socket_dir.cleanup()
(work/'probe.log').write_text(result.stdout+result.stderr);loaded=[]
for line in result.stdout.splitlines():
 if not line.startswith('LOADED '):continue
 path=line.removeprefix('LOADED ')
 if path=='linux-vdso.so.1':continue
 file=Path(path).resolve()
 if not file.is_relative_to(root):raise RuntimeError('Outside target loaded DSO: '+path)
 loaded.append({'path':str(file.relative_to(root)),'sha256':digest(file)})
if 'CUSTOM_DESKTOP_SERVICES_FOUNDATIONS_OK' not in result.stdout:raise RuntimeError('Missing target API success marker')
report={'success':True,'kind':'target-service-foundations-and-private-gio-ipc','scope':'GLib regex, JSON, Lua, JavaScript, GUdev type registration, actual WAV plugin loading and EXTERNAL-authenticated GIO IPC; desktop services and hardware need guest qualification','identity':identity,'sysroot':str(root),'packages':records,'loaded':loaded,'qualifier_sha256':digest(__file__),'source_sha256':digest(source),'compiler_sha256':digest(compiler)}
(work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'success':True,'report':str(work/'report.json'),'stdout':result.stdout},indent=2))
