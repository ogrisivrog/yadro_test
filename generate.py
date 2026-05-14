import json
import os
import xml.etree.ElementTree as ET


INPUT_XML = "impulse_test_input.xml"
CONFIG_JSON = "config.json"
PATCHED_JSON = "patched_config.json"
OUT_DIR = "outputs"


class XmlModel:
    def __init__(self, path):
        self.path = path
        self.classes = {}
        self.links = []
        self.nested = {}
        self.limits = {}

    def load(self):
        tree = ET.parse(self.path)
        root = tree.getroot()

        for cls in root.findall("Class"):
            cls_name = cls.get("name")
            root_flag = cls.get("isRoot", "false").lower() == "true"
            doc = cls.get("documentation", "")

            attrs = [
                {"name": x.get("name"), "type": x.get("type")}
                for x in cls.findall("Attribute")
            ]

            self.classes[cls_name] = {
                "isRoot": root_flag,
                "documentation": doc,
                "attributes": attrs,
            }

        for rel in root.findall("Aggregation"):
            src = rel.get("source")
            dst = rel.get("target")
            mult = rel.get("sourceMultiplicity", "1")

            self.links.append({
                "source": src,
                "target": dst,
                "sourceMultiplicity": mult,
            })

        self.nested = {x: [] for x in self.classes}

        for rel in self.links:
            src = rel["source"]
            dst = rel["target"]

            mn, mx = self.parse_mult(rel["sourceMultiplicity"])

            self.nested[dst].append({
                "name": src,
                "min": mn,
                "max": mx,
            })

            self.limits[src] = {
                "min": mn,
                "max": mx,
            }

    def parse_mult(self, s):
        if ".." in s:
            a, b = s.split("..")
            return a, b

        return s, s

    def get_root(self):
        for name, data in self.classes.items():
            if data["isRoot"]:
                return name

        raise ValueError("No root class found in model")


class XmlBuilder:
    def __init__(self, model: XmlModel):
        self.model = model

    def build(self, out_file):
        root_name = self.model.get_root()

        root = self.make_node(root_name)

        self.indent_xml(root)

        xml_data = ET.tostring(root, encoding="unicode")

        with open(out_file, "w", encoding="utf-8") as f:
            f.write(xml_data + "\n")

        print(f"config.xml -> {out_file}")

    def make_node(self, name):
        node = ET.Element(name)

        info = self.model.classes[name]

        for attr in info["attributes"]:
            item = ET.SubElement(node, attr["name"])
            item.text = attr["type"]

        for child in self.model.nested.get(name, []):
            node.append(self.make_node(child["name"]))

        return node

    def indent_xml(self, elem, lvl=0):
        pad = "\n" + "    " * lvl

        if len(elem):
            elem.text = pad + "    "

            for child in elem:
                self.indent_xml(child, lvl + 1)

            elem[-1].tail = pad

        if lvl == 0:
            elem.tail = "\n"
        else:
            elem.tail = pad


class MetaBuilder:
    def __init__(self, model: XmlModel):
        self.model = model

    def build(self, out_file):
        root = self.model.get_root()

        order = self.walk_tree(root)

        meta = []

        for name in order:
            info = self.model.classes[name]

            item = {
                "class": name,
                "documentation": info["documentation"],
                "isRoot": info["isRoot"],
            }

            if not info["isRoot"] and name in self.model.limits:
                item["max"] = self.model.limits[name]["max"]
                item["min"] = self.model.limits[name]["min"]

            fields = [
                {"name": x["name"], "type": x["type"]}
                for x in info["attributes"]
            ]

            for child in self.model.nested.get(name, []):
                fields.append({
                    "name": child["name"],
                    "type": "class",
                })

            item["parameters"] = fields

            meta.append(item)

        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=4, ensure_ascii=False)

        print(f"meta.json -> {out_file}")

    def walk_tree(self, root_name):
        used = []

        def dfs(name):
            for child in self.model.nested.get(name, []):
                dfs(child["name"])

            if name not in used:
                used.append(name)

        dfs(root_name)

        return used


class DeltaMaker:
    def __init__(self, cfg_path, patched_path):
        self.cfg_path = cfg_path
        self.patched_path = patched_path

        self.cfg = {}
        self.patched = {}
        self.delta = {}

    def load(self):
        with open(self.cfg_path, encoding="utf-8") as f:
            self.cfg = json.load(f)

        with open(self.patched_path, encoding="utf-8") as f:
            self.patched = json.load(f)

    def build(self):
        adds = [
            {"key": k, "value": v}
            for k, v in self.patched.items()
            if k not in self.cfg
        ]

        dels = [
            k for k in self.cfg
            if k not in self.patched
        ]

        upd = [
            {
                "key": k,
                "from": self.cfg[k],
                "to": self.patched[k],
            }
            for k in self.cfg
            if k in self.patched and self.cfg[k] != self.patched[k]
        ]

        self.delta = {
            "additions": adds,
            "deletions": dels,
            "updates": upd,
        }

    def save(self, out_file):
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(self.delta, f, indent=4, ensure_ascii=False)

        print(f"delta.json -> {out_file}")


class PatchApplier:
    def __init__(self, cfg, delta):
        self.cfg = cfg
        self.delta = delta

    def run(self, out_file):
        data = dict(self.cfg)

        for key in self.delta["deletions"]:
            data.pop(key, None)

        for item in self.delta["updates"]:
            data[item["key"]] = item["to"]

        for item in self.delta["additions"]:
            data[item["key"]] = item["value"]

        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

        print(f"res_patched_config.json -> {out_file}")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    model = XmlModel(INPUT_XML)
    model.load()

    XmlBuilder(model).build(os.path.join(OUT_DIR, "config.xml"))

    MetaBuilder(model).build(os.path.join(OUT_DIR, "meta.json"))

    delta = DeltaMaker(CONFIG_JSON, PATCHED_JSON)

    delta.load()
    delta.build()

    delta.save(os.path.join(OUT_DIR, "delta.json"))

    PatchApplier(delta.cfg, delta.delta).run(os.path.join(OUT_DIR, "res_patched_config.json"))


if __name__ == "__main__":
    main()