#!/usr/bin/env python3
"""Generate the dependency-free Xcode project. No downloaded generator required."""
import hashlib
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
objects = {}
def uid(key): return hashlib.sha256(key.encode()).hexdigest()[:24].upper()
def add(key, isa, **kwargs):
    objects[uid(key)] = dict(isa=isa, **kwargs)
    return uid(key)
def ref(path, kind): return add(path, 'PBXFileReference', lastKnownFileType=kind, path=path, sourceTree='<group>')
source_files = sorted('App/' + p.name for p in (ROOT/'App').glob('*.swift'))
shared = ref('Shared/PlaybackActivityAttributes.swift', 'sourcecode.swift')
intent = ref('Shared/RemotePlaybackIntent.swift', 'sourcecode.swift')
widget_source = ref('LiveActivity/ArrifyLiveActivity.swift', 'sourcecode.swift')
files = [ref(p, 'sourcecode.swift') for p in source_files] + [shared, intent]
test_file = ref('UITests/LaunchTests.swift', 'sourcecode.swift')
app_test_file = ref('AppTests/NowPlayingArtworkTests.swift', 'sourcecode.swift')
plist = ref('App/Info.plist', 'text.plist.xml')
privacy = ref('App/PrivacyInfo.xcprivacy', 'text.xml')
assets = ref('App/Assets.xcassets', 'folder.assetcatalog')
product = add('app-product', 'PBXFileReference', explicitFileType='wrapper.application', path='Arrify.app', sourceTree='BUILT_PRODUCTS_DIR')
test_product = add('test-product', 'PBXFileReference', explicitFileType='wrapper.cfbundle', path='ArrifyUITests.xctest', sourceTree='BUILT_PRODUCTS_DIR')
app_test_product = add('app-test-product', 'PBXFileReference', explicitFileType='wrapper.cfbundle', path='ArrifyAppTests.xctest', sourceTree='BUILT_PRODUCTS_DIR')
widget_product = add('widget-product', 'PBXFileReference', explicitFileType='wrapper.app-extension', path='ArrifyLiveActivity.appex', sourceTree='BUILT_PRODUCTS_DIR')
products = add('products', 'PBXGroup', children=[product, test_product, app_test_product, widget_product], name='Products', sourceTree='<group>')
group = add('root', 'PBXGroup', children=files + [widget_source, test_file, app_test_file, plist, privacy, assets, products], sourceTree='<group>')
package = add('package', 'XCLocalSwiftPackageReference', relativePath='.')
package_product = add('core', 'XCSwiftPackageProductDependency', package=package, productName='ArrifyCore')
def phase(key, kind, refs):
    builds = [add(key + r, 'PBXBuildFile', **({'productRef': r} if r == package_product else {'fileRef': r})) for r in refs]
    return add(key, kind, buildActionMask=2147483647, files=builds, runOnlyForDeploymentPostprocessing=0)
def configs(key, settings):
    ids=[]
    for mode in ('Debug', 'Release'):
        values=dict(settings)
        if mode == 'Debug':
            values['SWIFT_ACTIVE_COMPILATION_CONDITIONS'] = 'DEBUG' + (' ARRIFY_APP' if key == 'app' else '')
            values['ONLY_ACTIVE_ARCH'] = 'YES'
            values['ENABLE_TESTABILITY'] = 'YES'
        if mode == 'Release' and key == 'app': values['SWIFT_ACTIVE_COMPILATION_CONDITIONS'] = 'ARRIFY_APP'
        values['SWIFT_OPTIMIZATION_LEVEL'] = '-Onone' if mode=='Debug' else '-O'
        values['DEBUG_INFORMATION_FORMAT'] = 'dwarf' if mode=='Debug' else 'dwarf-with-dsym'
        ids.append(add(key+mode, 'XCBuildConfiguration', buildSettings=values, name=mode))
    return add(key+'configs', 'XCConfigurationList', buildConfigurations=ids, defaultConfigurationIsVisible=0, defaultConfigurationName='Release')
base={'SDKROOT':'iphoneos','IPHONEOS_DEPLOYMENT_TARGET':'18.0','SWIFT_VERSION':'6.0','CLANG_ENABLE_MODULES':'YES','SWIFT_STRICT_CONCURRENCY':'complete','TARGETED_DEVICE_FAMILY':'1,2','CODE_SIGN_STYLE':'Automatic','DEVELOPMENT_TEAM':'VW3VH2HPTV'}
app_settings={**base,'PRODUCT_BUNDLE_IDENTIFIER':'com.robnets.arrify','PRODUCT_NAME':'$(TARGET_NAME)','INFOPLIST_FILE':'App/Info.plist','CODE_SIGN_ENTITLEMENTS':'App/Arrify.entitlements','CURRENT_PROJECT_VERSION':'1','MARKETING_VERSION':'0.3.0','GENERATE_INFOPLIST_FILE':'NO','ASSETCATALOG_COMPILER_APPICON_NAME':'AppIcon','SUPPORTED_PLATFORMS':'iphoneos iphonesimulator'}
app=add('app', 'PBXNativeTarget', buildConfigurationList=configs('app',app_settings), buildPhases=[phase('sources','PBXSourcesBuildPhase',files),phase('frameworks','PBXFrameworksBuildPhase',[package_product]),phase('resources','PBXResourcesBuildPhase',[privacy,assets])], buildRules=[], dependencies=[], name='Arrify', packageProductDependencies=[package_product], productName='Arrify', productReference=product, productType='com.apple.product-type.application')
widget_settings={**base, 'PRODUCT_BUNDLE_IDENTIFIER':'com.robnets.arrify.liveactivity', 'PRODUCT_NAME':'$(TARGET_NAME)', 'INFOPLIST_FILE':'LiveActivity/Info.plist', 'CURRENT_PROJECT_VERSION':'1', 'MARKETING_VERSION':'0.3.0', 'GENERATE_INFOPLIST_FILE':'NO', 'APPLICATION_EXTENSION_API_ONLY':'YES', 'SKIP_INSTALL':'YES', 'LD_RUNPATH_SEARCH_PATHS':'$(inherited) @executable_path/Frameworks @executable_path/../../Frameworks'}
widget=add('widget', 'PBXNativeTarget', buildConfigurationList=configs('widget',widget_settings), buildPhases=[phase('widget-sources','PBXSourcesBuildPhase',[shared,intent,widget_source]),phase('widget-frameworks','PBXFrameworksBuildPhase',[])],buildRules=[],dependencies=[],name='ArrifyLiveActivity',productName='ArrifyLiveActivity',productReference=widget_product,productType='com.apple.product-type.app-extension')
widget_proxy=add('widget-proxy','PBXContainerItemProxy',containerPortal=uid('project'),proxyType=1,remoteGlobalIDString=widget,remoteInfo='ArrifyLiveActivity')
widget_dependency=add('widget-dependency','PBXTargetDependency',target=widget,targetProxy=widget_proxy)
embed_build=add('widget-embed-build','PBXBuildFile',fileRef=widget_product,settings={'ATTRIBUTES':['RemoveHeadersOnCopy']})
embed=add('widget-embed','PBXCopyFilesBuildPhase',buildActionMask=2147483647,files=[embed_build],runOnlyForDeploymentPostprocessing=0,dstPath='',dstSubfolderSpec=13,name='Embed App Extensions')
objects[app]['buildPhases'].append(embed)
objects[app]['dependencies'].append(widget_dependency)
proxy=add('proxy','PBXContainerItemProxy',containerPortal=uid('project'),proxyType=1,remoteGlobalIDString=app,remoteInfo='Arrify')
dependency=add('dependency','PBXTargetDependency',target=app,targetProxy=proxy)
tests=add('tests','PBXNativeTarget', buildConfigurationList=configs('tests',{**base,'PRODUCT_BUNDLE_IDENTIFIER':'com.robnets.arrify.uitests','PRODUCT_NAME':'$(TARGET_NAME)','GENERATE_INFOPLIST_FILE':'YES','TEST_TARGET_NAME':'Arrify'}),buildPhases=[phase('test-sources','PBXSourcesBuildPhase',[test_file]),phase('test-frameworks','PBXFrameworksBuildPhase',[])],buildRules=[],dependencies=[dependency],name='ArrifyUITests',productName='ArrifyUITests',productReference=test_product,productType='com.apple.product-type.bundle.ui-testing')
app_tests=add('app-tests', 'PBXNativeTarget', buildConfigurationList=configs('app-tests',{**base,'PRODUCT_BUNDLE_IDENTIFIER':'com.robnets.arrify.apptests','PRODUCT_NAME':'$(TARGET_NAME)','GENERATE_INFOPLIST_FILE':'YES','TEST_HOST':'$(BUILT_PRODUCTS_DIR)/Arrify.app/$(BUNDLE_EXECUTABLE_FOLDER_PATH)/Arrify','BUNDLE_LOADER':'$(TEST_HOST)'}),buildPhases=[phase('app-test-sources','PBXSourcesBuildPhase',[app_test_file]),phase('app-test-frameworks','PBXFrameworksBuildPhase',[package_product])],buildRules=[],dependencies=[dependency],name='ArrifyAppTests',packageProductDependencies=[package_product],productName='ArrifyAppTests',productReference=app_test_product,productType='com.apple.product-type.bundle.unit-test')
project=add('project','PBXProject',attributes={'LastUpgradeCheck':'2600'},buildConfigurationList=configs('project',base),compatibilityVersion='Xcode 14.0',developmentRegion='en',hasScannedForEncodings=0,knownRegions=['en','Base'],mainGroup=group,packageReferences=[package],productRefGroup=products,projectDirPath='',projectRoot='',targets=[app,tests,app_tests,widget])
def render(v, indent=0):
    if isinstance(v,dict): return '{\n'+''.join('\t'*(indent+1)+json.dumps(k)+' = '+render(val,indent+1)+';\n' for k,val in v.items())+'\t'*indent+'}'
    if isinstance(v,list): return '( '+', '.join(render(x,indent) for x in v)+' )'
    return str(v) if isinstance(v,int) else json.dumps(v)
pbx={'archiveVersion':1,'classes':{},'objectVersion':56,'objects':objects,'rootObject':project}
(ROOT/'Arrify.xcodeproj/project.pbxproj').write_text('// !$*UTF8*$!\n'+render(pbx)+'\n')
def buildref(target,name,build): return f'<BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{target}" BuildableName="{build}" BlueprintName="{name}" ReferencedContainer="container:Arrify.xcodeproj"/>'
appref=buildref(app,'Arrify','Arrify.app'); testref=buildref(tests,'ArrifyUITests','ArrifyUITests.xctest'); apptestref=buildref(app_tests,'ArrifyAppTests','ArrifyAppTests.xctest')
(ROOT/'Arrify.xcodeproj/xcshareddata/xcschemes/Arrify.xcscheme').write_text(f'''<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion="2600" version="1.3">
<BuildAction parallelizeBuildables="YES" buildImplicitDependencies="YES"><BuildActionEntries><BuildActionEntry buildForTesting="YES" buildForRunning="YES" buildForProfiling="YES" buildForArchiving="YES" buildForAnalyzing="YES">{appref}</BuildActionEntry></BuildActionEntries></BuildAction>
<TestAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" shouldUseLaunchSchemeArgsEnv="YES"><Testables><TestableReference skipped="NO">{testref}</TestableReference><TestableReference skipped="NO">{apptestref}</TestableReference></Testables></TestAction>
<LaunchAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" launchStyle="0" useCustomWorkingDirectory="NO" ignoresPersistentStateOnLaunch="NO" debugDocumentVersioning="YES" debugServiceExtension="internal" allowLocationSimulation="YES"><BuildableProductRunnable runnableDebuggingMode="0">{appref}</BuildableProductRunnable></LaunchAction>
<ProfileAction buildConfiguration="Release" shouldUseLaunchSchemeArgsEnv="YES" savedToolIdentifier="" useCustomWorkingDirectory="NO" debugDocumentVersioning="YES"><BuildableProductRunnable runnableDebuggingMode="0">{appref}</BuildableProductRunnable></ProfileAction>
<AnalyzeAction buildConfiguration="Debug"/><ArchiveAction buildConfiguration="Release" revealArchiveInOrganizer="YES"/>
</Scheme>''')
