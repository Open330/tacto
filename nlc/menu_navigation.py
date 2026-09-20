"""Observed menu navigation for the general planner; paths never come from model text."""
import json

class MenuNavigation:
    def __init__(self, driver, target, capabilities):
        self.driver, self.target = driver, target
        self.supported = 'menu_items' in capabilities and callable(getattr(driver,'menu_items',None))
        self.executable = 'action.menu' in capabilities
        self.path = None
        self.items = []

    def refresh(self):
        if self.path is None:
            return
        reply = self.driver.menu_items(self.target,self.path)
        if not isinstance(reply,dict) or reply.get('type')!='menu_items' or reply.get('complete') is not True or reply.get('path')!=self.path:
            raise ValueError('menu listing unavailable: '+str(reply.get('error') if isinstance(reply,dict) else 'malformed'))
        rows=reply.get('items')
        if not isinstance(rows,list):raise ValueError('malformed menu items')
        seen=set()
        for row in rows:
            if not isinstance(row,dict) or not {'title','path','enabled','has_submenu'} <= row.keys():raise ValueError('malformed menu item')
            title=row.get('title')
            if not isinstance(title,str) or not title or len(title)>200 or any(ord(c)<32 or ord(c)==127 for c in title):
                raise ValueError('invalid menu title')
            if title in seen or row.get('path')!=[*self.path,title]:raise ValueError('ambiguous menu path')
            seen.add(title)
            if any(row.get(k) is not None and type(row[k]) is not bool for k in ('enabled','has_submenu')):
                raise ValueError('invalid menu state')
        self.items=rows

    def candidates(self):
        if not self.supported:return []
        def candidate(path,label,can,disabled=False):
            return {'element_index':-1,'role':'MenuPath','label':label,'match':json.dumps(path,ensure_ascii=False),
                    'attr':'Title','container':'','where':'메뉴: '+(' > '.join(path) or '메뉴 막대'),
                    'can':can,'disabled':disabled,'line':'MenuItem Title: '+json.dumps(' > '.join(path),ensure_ascii=False),
                    'menu_path':list(path)}
        result=[candidate([],'메뉴 목록 조회',['inspect_menu'])]
        if self.path:
            result.append(candidate(self.path[:-1],'상위 메뉴 목록 조회',['inspect_menu']))
        for row in self.items:
            can=['inspect_menu'] if row['has_submenu'] is not False and len(row['path'])<=6 else []
            if row['has_submenu'] is False and row['enabled'] is True and self.executable and len(row['path'])<=6:
                can.append('menu')
            result.append(candidate(row['path'],row['title'],can,not can))
        return result

    def inspect(self,candidate):
        if candidate not in self.candidates() or 'inspect_menu' not in candidate.get('can',[]):
            raise ValueError('unobserved menu navigation')
        self.path=list(candidate['menu_path'])
        self.refresh()

    def validate_command(self,candidate):
        if not self.executable or candidate not in self.candidates() or 'menu' not in candidate.get('can',[]):
            raise ValueError('unobserved menu command')
        self.refresh()
        if candidate not in self.candidates():raise ValueError('menu changed before dispatch')
