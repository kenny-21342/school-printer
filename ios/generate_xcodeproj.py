#!/usr/bin/env python3
"""
Generate a minimal, valid Xcode project (.xcodeproj/project.pbxproj) for the
SchoolPrinter iOS app. Avoids needing XcodeGen or CocoaPods.

Usage: python3 generate_xcodeproj.py <src_dir> <bundle_id>
"""
from __future__ import annotations
import os, sys, uuid, hashlib
from pathlib import Path

SRC_DIR = Path(sys.argv[1] if len(sys.argv) > 1 else "SchoolPrinter")
BUNDLE_ID = sys.argv[2] if len(sys.argv) > 2 else "com.kenny.schoolprinter"
PRODUCT_NAME = "SchoolPrinter"
IOS_DEPLOY = "17.0"

def make_id(*parts: str) -> str:
    """Stable 24-char hex UUID based on content, so reruns are diff-stable."""
    h = hashlib.sha1("\x00".join(parts).encode()).hexdigest()[:24].upper()
    return h

# Collect .swift files recursively
swift_files: list[Path] = sorted([p for p in SRC_DIR.rglob("*.swift")])

# Xcode uses 96-bit (24 hex) identifiers. We prefix with a constant byte so they
# look like real UUIDs but stay deterministic.
def FID(name: str) -> str: return make_id(name)

project_id   = FID("ProjectObject")
main_group   = FID("MainGroup")
src_group_id = FID("SrcGroup_" + SRC_DIR.name)
views_group  = FID("ViewsGroup")
build_phases = FID("SourcesBuildPhase")
resources_phases = FID("ResourcesBuildPhase")
target_id     = FID("Target_" + PRODUCT_NAME)
product_file  = FID("ProductFile_" + PRODUCT_NAME)
build_configs_list = FID("BuildConfigsList")
xc_config_list_proj = FID("XCConfigListProject")
xc_config_list_target = FID("XCConfigListTarget")
debug_proj    = FID("DebugProject")
release_proj  = FID("ReleaseProject")
debug_target  = FID("DebugTarget")
release_target = FID("ReleaseTarget")
build_file_ids: dict[str, str] = {}
file_ref_ids: dict[str, str] = {}

for f in swift_files:
    file_ref_ids[str(f)] = FID("FileRef_" + str(f))
    build_file_ids[str(f)] = FID("BuildFile_" + str(f))

PBX_HEADER = """// !$*UTF8*$!
{
	archiveVersion = 1;
	classes = {};
	objectVersion = 56;
	objects = {
"""

PBX_FOOTER = """	};
	rootObject = %s;
}
""" % project_id

out = [PBX_HEADER]

# --- PBXBuildFile entries (swift sources) ---
for f in swift_files:
    out.append(f"\t{build_file_ids[str(f)]} /* {f.name} in Sources */ = {{isa = PBXBuildFile; fileRef = {file_ref_ids[str(f)]} /* {f.name} */; }};\n")

# --- PBXFileReference: product ---
out.append(f"\t{product_file} /* {PRODUCT_NAME}.app */ = {{isa = PBXFileReference; explicitFileType = wrapper.application; includeInIndex = 0; path = {PRODUCT_NAME}.app; sourceTree = BUILT_PRODUCTS_DIR; }};\n")

# --- PBXFileReference: swift files ---
for f in swift_files:
    rel = f.relative_to(SRC_DIR.parent)
    out.append(f"\t{file_ref_ids[str(f)]} /* {f.name} */ = {{isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = \"{f.name}\"; sourceTree = \"<group>\"; }};\n")

# --- PBXGroup: main / src / views ---
def emit_group(gid: str, name: str, children: list[str], indent: str = "\t") -> str:
    kids = "\n".join(f"\t\t\t{c}," for c in children)
    return (f"\t{gid} /* {name} */ = {{\n"
            f"\t\tisa = PBXGroup;\n"
            f"\t\tchildren = (\n{kids}\n\t\t);\n"
            f"\t\tname = \"{name}\";\n"
            f"\t\tsourceTree = \"<group>\";\n"
            f"\t}};\n")

def emit_group_with_path(gid: str, path: str, children: list[str]) -> str:
    kids = "\n".join(f"\t\t\t{c}," for c in children)
    return (f"\t{gid} /* {path} */ = {{\n"
            f"\t\tisa = PBXGroup;\n"
            f"\t\tchildren = (\n{kids}\n\t\t);\n"
            f"\t\tpath = \"{path}\";\n"
            f"\t\tsourceTree = \"<group>\";\n"
            f"\t}};\n")

# Views subgroup
view_files = [f for f in swift_files if "Views" in f.parts]
non_view_files = [f for f in swift_files if "Views" not in f.parts]

view_child_refs = [file_ref_ids[str(f)] for f in view_files]
out.append(emit_group_with_path(views_group, "Views", view_child_refs))

# Src group: non-view files + Views subgroup
src_children = [file_ref_ids[str(f)] for f in non_view_files] + [views_group]
out.append(emit_group_with_path(src_group_id, SRC_DIR.name, src_children))

# Main group
main_children = [src_group_id, product_file]
out.append(emit_group_with_path(main_group, "", main_children).replace('path = "";', 'path = "";'.replace('""', '""')))

# fix main group: should have no path, name empty
out[-1] = (f"\t{main_group} = {{\n"
           f"\t\tisa = PBXGroup;\n"
           f"\t\tchildren = (\n"
           + "\n".join(f"\t\t\t{c}," for c in main_children) + "\n"
           f"\t\t);\n"
           f"\t\tsourceTree = \"<group>\";\n"
           f"\t}};\n")

# --- PBXSourcesBuildPhase ---
build_file_refs = [build_file_ids[str(f)] for f in swift_files]
out.append("\t" + build_phases + " /* Sources */ = {\n"
           "\t\tisa = PBXSourcesBuildPhase;\n"
           "\t\tbuildActionMask = 2147483647;\n"
           "\t\tfiles = (\n"
           + "\n".join(f"\t\t\t{r} in Sources," for r in build_file_refs) + "\n"
           "\t\t);\n"
           "\t\trunOnlyForDeploymentPostprocessing = 0;\n"
           "\t};\n")

# empty resources phase (kept for future assets)
out.append("\t" + resources_phases + " /* Resources */ = {\n"
           "\t\tisa = PBXResourcesBuildPhase;\n"
           "\t\tbuildActionMask = 2147483647;\n"
           "\t\tfiles = (\n\t\t);\n"
           "\t\trunOnlyForDeploymentPostprocessing = 0;\n"
           "\t};\n")

# --- PBXNativeTarget ---
out.append("\t" + target_id + f" /* {PRODUCT_NAME} */ = {{\n"
           "\t\tisa = PBXNativeTarget;\n"
           f"\t\tbuildConfigurationList = {xc_config_list_target};\n"
           "\t\tbuildPhases = (\n"
           f"\t\t\t{build_phases} /* Sources */,\n"
           f"\t\t\t{resources_phases} /* Resources */,\n"
           "\t\t);\n"
           "\t\tbuildRules = (\n\t\t);\n"
           "\t\tdependencies = (\n\t\t);\n"
           f"\t\tname = \"{PRODUCT_NAME}\";\n"
           f"\t\tproductName = \"{PRODUCT_NAME}\";\n"
           f"\t\tproductReference = {product_file} /* {PRODUCT_NAME}.app */;\n"
           "\t\tproductType = \"com.apple.product-type.application\";\n"
           "\t};\n")

# --- XCBuildConfiguration: project Debug/Release ---
def proj_config(gid: str, name: str) -> str:
    return ("\t" + gid + f" /* {name} */ = {{\n"
            "\t\tisa = XCBuildConfiguration;\n"
            "\t\tbuildSettings = {\n"
            "\t\t\tALWAYS_SEARCH_USER_PATHS = NO;\n"
            "\t\t\tCLANG_ENABLE_MODULES = YES;\n"
            "\t\t\t\"CODE_SIGN_IDENTITY[sdk=macosx*]\" = \"-\";\n"
            "\t\t\tCOPY_PHASE_STRIP = NO;\n"
            "\t\t\tDEBUG_INFORMATION_FORMAT = \"dwarf-with-dsym\";\n"
            f"\t\t\tIPHONEOS_DEPLOYMENT_TARGET = {IOS_DEPLOY};\n"
            "\t\t\tSDKROOT = iphoneos;\n"
            "\t\t\tSWIFT_OPTIMIZATION_LEVEL = \"-Onone\";\n" if name == "Debug" else ""
            "\t\t};\n"
            f"\t\tname = \"{name}\";\n"
            "\t};\n")

out.append("\t" + debug_proj + " /* Debug */ = {\n"
           "\t\tisa = XCBuildConfiguration;\n"
           "\t\tbuildSettings = {\n"
           "\t\t\tALWAYS_SEARCH_USER_PATHS = NO;\n"
           "\t\t\tCLANG_ENABLE_MODULES = YES;\n"
           "\t\t\tIPHONEOS_DEPLOYMENT_TARGET = " + IOS_DEPLOY + ";\n"
           "\t\t\tSDKROOT = iphoneos;\n"
           "\t\t\tSWIFT_OPTIMIZATION_LEVEL = \"-Onone\";\n"
           "\t\t};\n"
           "\t\tname = Debug;\n"
           "\t};\n")
out.append("\t" + release_proj + " /* Release */ = {\n"
           "\t\tisa = XCBuildConfiguration;\n"
           "\t\tbuildSettings = {\n"
           "\t\t\tALWAYS_SEARCH_USER_PATHS = NO;\n"
           "\t\t\tCLANG_ENABLE_MODULES = YES;\n"
           "\t\t\tIPHONEOS_DEPLOYMENT_TARGET = " + IOS_DEPLOY + ";\n"
           "\t\t\tSDKROOT = iphoneos;\n"
           "\t\t\tSWIFT_OPTIMIZATION_LEVEL = \"-O\";\n"
           "\t\t};\n"
           "\t\tname = Release;\n"
           "\t};\n")

# --- XCBuildConfiguration: target Debug/Release ---
def target_config(gid: str, name: str, swift_opt: str) -> str:
    return ("\t" + gid + f" /* {name} */ = {{\n"
            "\t\tisa = XCBuildConfiguration;\n"
            "\t\tbuildSettings = {\n"
            "\t\t\tASSETCATALOG_COMPILER_GLOBAL_ACCENT_COLOR_NAME = AccentColor;\n"
            f"\t\t\tCODE_SIGN_STYLE = Automatic;\n"
            f"\t\t\tCURRENT_PROJECT_VERSION = 1;\n"
            f"\t\t\tGENERATE_INFOPLIST_FILE = YES;\n"
            f"\t\t\tINFOPLIST_KEY_CFBundleDisplayName = \"School Printer\";\n"
            f"\t\t\tINFOPLIST_KEY_UIApplicationSceneManifest_Generation = YES;\n"
            f"\t\t\tINFOPLIST_KEY_UIApplicationSupportsIndirectInputEvents = YES;\n"
            f"\t\t\tINFOPLIST_KEY_UILaunchScreen_Generation = YES;\n"
            f"\t\t\tINFOPLIST_KEY_UISupportedInterfaceOrientations_iad = \"UIInterfaceOrientationPortrait UIInterfaceOrientationPortraitUpsideDown UIInterfaceOrientationLandscapeLeft UIInterfaceOrientationLandscapeRight\";\n"
            f"\t\t\tINFOPLIST_KEY_NSLocalNetworkUsageDescription = \"The app connects to a print server on your school network.\";\n"
            f"\t\t\tLD_RUNPATH_SEARCH_PATHS = (\n"
            "\t\t\t\t\"$(inherited)\",\n"
            "\t\t\t\t\"@executable_path/Frameworks\",\n"
            "\t\t\t);\n"
            f"\t\t\tMARKETING_VERSION = 1.0;\n"
            f"\t\t\tPRODUCT_BUNDLE_IDENTIFIER = \"{BUNDLE_ID}\";\n"
            f"\t\t\tPRODUCT_NAME = \"$(TARGET_NAME)\";\n"
            f"\t\t\tSWIFT_EMIT_LOC_STRINGS = YES;\n"
            f"\t\t\tSWIFT_OPTIMIZATION_LEVEL = \"{swift_opt}\";\n"
            f"\t\t\tTARGETED_DEVICE_FAMILY = \"1,2\";\n"
            "\t\t};\n"
            f"\t\tname = \"{name}\";\n"
            "\t};\n")

out.append(target_config(debug_target, "Debug", "-Onone"))
out.append(target_config(release_target, "Release", "-O"))

# --- XCConfigurationList: project ---
out.append("\t" + xc_config_list_proj + " /* Build configuration list for PBXProject */ = {\n"
           "\t\tisa = XCConfigurationList;\n"
           "\t\tbuildConfigurations = (\n"
           f"\t\t\t{debug_proj} /* Debug */,\n"
           f"\t\t\t{release_proj} /* Release */,\n"
           "\t\t);\n"
           "\t\tdefaultConfigurationName = Release;\n"
           "\t\tdefaultConfigurationIsVisible = 0;\n"
           "\t};\n")
out.append("\t" + xc_config_list_target + f" /* Build configuration list for PBXNativeTarget \"{PRODUCT_NAME}\" */ = {{\n"
           "\t\tisa = XCConfigurationList;\n"
           "\t\tbuildConfigurations = (\n"
           f"\t\t\t{debug_target} /* Debug */,\n"
           f"\t\t\t{release_target} /* Release */,\n"
           "\t\t);\n"
           "\t\tdefaultConfigurationName = Release;\n"
           "\t\tdefaultConfigurationIsVisible = 0;\n"
           "\t};\n")

# --- PBXProject ---
out.append("\t" + project_id + " /* Project object */ = {\n"
           "\t\tisa = PBXProject;\n"
           "\t\tbuildConfigurationList = " + xc_config_list_proj + " /* Build configuration list for PBXProject */;\n"
           "\t\tcompatibilityVersion = \"Xcode 14.0\";\n"
           "\t\tdevelopmentRegion = en;\n"
           "\t\thasScannedForEncodings = 0;\n"
           "\t\tknownRegions = (en, Base);\n"
           "\t\tmainGroup = " + main_group + ";\n"
           f"\t\tproductRefGroup = {main_group};\n"
           "\t\tprojectDirPath = \"\";\n"
           "\t\tprojectRoot = \"\";\n"
           "\t\ttargets = (\n"
           f"\t\t\t{target_id} /* {PRODUCT_NAME} */,\n"
           "\t\t);\n"
           "\t};\n")

out.append(PBX_FOOTER)

text = "".join(out)
output_path = Path(PRODUCT_NAME + ".xcodeproj") / "project.pbxproj"
(Path(PRODUCT_NAME + ".xcodeproj")).mkdir(exist_ok=True)
output_path.write_text(text)
print(f"Wrote {output_path} ({len(text)} bytes, {len(swift_files)} sources)")
