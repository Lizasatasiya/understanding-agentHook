import subprocess
import ast

class ContextBuilder:
    def build(self, changes: dict, graph) -> dict:
        
        changed_code = changes.get("files", [])
        
        # Determine all functions that were modified in the commit
        changed_funcs = set()
        for f in changed_code:
            for func in f.get("changed_functions", []):
                changed_funcs.add(func["name"])
                
        # Build dependency summaries
        dep_summaries = {}
        
        # Extract dependencies discovered by the CodeGraph
        for node_id, data in graph.nodes.items():
            if data["type"] == "function" and data["name"] not in changed_funcs:
                # Find which file it was defined in
                defined_file = None
                for edge in graph.edges:
                    if edge["source"] == node_id and edge["relation"] == "defined_in":
                        defined_file = edge["target"].split(":", 1)[1]
                        break
                
                if defined_file:
                    details = self._get_function_details(defined_file, data["name"])
                    
                    # find what this calls
                    calls = []
                    for edge in graph.edges:
                        if edge["source"] == node_id and edge["relation"] == "calls":
                            calls.append(edge["target"].split(":", 1)[1])
                            
                    dep_summaries[data["name"]] = {
                        "defined_in": defined_file,
                        "details": details,
                        "calls": calls
                    }
        
        structured_changes = []
        for f in changed_code:
            filepath = f["path"]
            
            # Get old and new source
            try:
                old_source = subprocess.check_output(["git", "show", f"HEAD:{filepath}"], text=True, stderr=subprocess.DEVNULL)
            except subprocess.CalledProcessError:
                old_source = ""
                
            try:
                new_source = subprocess.check_output(["git", "show", f":{filepath}"], text=True, stderr=subprocess.DEVNULL)
            except subprocess.CalledProcessError:
                new_source = ""
                
            for func in f.get("changed_functions", []):
                func_name = func["name"]
                added_calls = func.get("added_calls", [])
                
                dep_text = self._build_dependency_summary(func_name, added_calls, dep_summaries)
                
                # Extract function diff
                old_func_body = self._extract_function_source(old_source, func_name)
                new_func_body = self._extract_function_source(new_source, func_name)
                
                # Skip if the function body is unchanged (hunk overlap false-positive)
                if old_func_body == new_func_body and old_func_body != "":
                    continue
                
                import difflib
                diff_lines = list(difflib.unified_diff(
                    old_func_body.splitlines(keepends=True),
                    new_func_body.splitlines(keepends=True),
                    fromfile=f"a/{filepath} ({func_name})",
                    tofile=f"b/{filepath} ({func_name})",
                    n=3
                ))
                func_diff = "".join(diff_lines)
                if not func_diff:
                    func_diff = f"New function added: {func_name}"
                
                structured_changes.append({
                    "file": filepath,
                    "function": func_name,
                    "diff": func_diff,
                    "dependency_summary": dep_text
                })
        
        context = {
            "structured_changes": structured_changes,
            "raw_dependencies": dep_summaries,
            "tests": [], # Skipping tests for this small prototype
            "documentation": [],
            "graph_relationships": graph.edges
        }
        
        entities = len(structured_changes) + len(dep_summaries)
        return context

    def _get_function_details(self, filepath: str, func_name: str) -> dict:
        try:
            with open(filepath, 'r') as f:
                source = f.read()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                    args = [arg.arg for arg in node.args.args]
                    
                    # Extract return type
                    returns = "None"
                    if node.returns:
                        if hasattr(ast, 'unparse'):
                            returns = ast.unparse(node.returns)
                        elif hasattr(node.returns, 'id'):
                            returns = node.returns.id
                            
                    docstring = ast.get_docstring(node)
                    is_async = isinstance(node, ast.AsyncFunctionDef)
                    
                    return {
                        "args": args,
                        "returns": returns,
                        "docstring": docstring or "No description.",
                        "is_async": is_async
                    }
        except Exception:
            pass
        return {"args": [], "returns": "unknown", "docstring": "No description.", "is_async": False}
        
    def _extract_function_source(self, source: str, func_name: str) -> str:
        if not source:
            return ""
        try:
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                    return ast.get_source_segment(source, node) or ""
        except SyntaxError:
            pass
        return ""
        
    def _build_dependency_summary(self, func_name: str, direct_calls: list, dep_summaries: dict) -> str:
        if not direct_calls:
            return "No external dependencies called."
            
        summary_lines = []
        for call in direct_calls:
            if call in dep_summaries:
                info = dep_summaries[call]
                details = info['details']
                async_str = "an async function" if details['is_async'] else "a function"
                ret_str = f"returns {details['returns']}"
                
                doc = details['docstring'].replace('\n', ' ')
                line = f"- `{call}({', '.join(details['args'])})` is {async_str} defined in `{info['defined_in']}`. It {ret_str}. Description: {doc}"
                
                if info['calls']:
                    line += f" It in turn calls: {', '.join(info['calls'])}."
                    
                    for sub_call in info['calls']:
                        if sub_call in dep_summaries:
                            sub_info = dep_summaries[sub_call]
                            sub_details = sub_info['details']
                            sub_async = "an async function" if sub_details['is_async'] else "a function"
                            sub_ret = f"returns {sub_details['returns']}"
                            sub_doc = sub_details['docstring'].replace('\n', ' ')
                            line += f"\n    - `{sub_call}({', '.join(sub_details['args'])})` is {sub_async}. It {sub_ret}. Description: {sub_doc}"
                            
                summary_lines.append(line)
                
        return "\n".join(summary_lines)
