import subprocess
import ast

class CodeGraph:
    def __init__(self):
        self.nodes = {}
        self.edges = []
        
    def _add_node(self, node_id, node_type, name):
        if node_id not in self.nodes:
            self.nodes[node_id] = {"type": node_type, "name": name}
            
    def _add_edge(self, source, target, relation):
        edge = {"source": source, "target": target, "relation": relation}
        if edge not in self.edges:
            self.edges.append(edge)
            
    def _find_definition(self, func_name: str) -> str:
        try:
            grep_out = subprocess.check_output(
                ["git", "grep", "-n", f"def {func_name}"],
                text=True, stderr=subprocess.DEVNULL
            )
            if grep_out:
                return grep_out.split(':')[0]
        except subprocess.CalledProcessError:
            pass
        return None

    def _get_calls_in_function(self, filepath: str, func_name: str) -> list:
        try:
            with open(filepath, 'r') as f:
                source = f.read()
            tree = ast.parse(source)
            calls = []
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == func_name:
                    for sub_node in ast.walk(node):
                        if isinstance(sub_node, ast.Call) and isinstance(sub_node.func, ast.Name):
                            calls.append(sub_node.func.id)
                    break
            return calls
        except Exception:
            return []

    def build(self, changed_files: list):
        
        to_analyze = []
        
        for f in changed_files:
            file_node = f"file:{f['path']}"
            self._add_node(file_node, "file", f['path'])
            
            for func in f.get('changed_functions', []):
                func_node = f"func:{func['name']}"
                self._add_node(func_node, "function", func['name'])
                self._add_edge(file_node, func_node, "contains")
                self._add_edge(func_node, file_node, "defined_in")
                
                for call in func.get('added_calls', []):
                    to_analyze.append((func['name'], call, 1))
                    
        analyzed_calls = set()
        
        while to_analyze:
            caller_name, call_name, depth = to_analyze.pop(0)
            if (caller_name, call_name) in analyzed_calls:
                continue
            analyzed_calls.add((caller_name, call_name))
            
            call_node = f"func:{call_name}"
            self._add_node(call_node, "function", call_name)
            caller_node = f"func:{caller_name}"
            self._add_edge(caller_node, call_node, "calls")
            
            defined_file = self._find_definition(call_name)
            if defined_file:
                file_node = f"file:{defined_file}"
                self._add_node(file_node, "file", defined_file)
                self._add_edge(file_node, call_node, "contains")
                self._add_edge(call_node, file_node, "defined_in")
                
                if depth < 2:
                    sub_calls = self._get_calls_in_function(defined_file, call_name)
                    for sub_call in sub_calls:
                        to_analyze.append((call_name, sub_call, depth + 1))
                        

    def get_related_functions(self, func_name: str):
        related = []
        for edge in self.edges:
            if edge["source"] == f"func:{func_name}" and edge["relation"] == "calls":
                related.append(edge["target"].split(":", 1)[1])
        return related
