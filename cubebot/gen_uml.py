import codecs
import sadisplay
import model
import config
from os import path

desc = sadisplay.describe([getattr(model, attr) for attr in dir(model)])
with codecs.open(path.join(config.project_dir,'doc','db_model.plantuml'), 'w', encoding='utf-8') as f:
    f.write(sadisplay.plantuml(desc))
# Open fresh UML here to generate PNG :
# http://www.plantuml.com/plantuml/uml/

    
##with codecs.open('schema.dot', 'w', encoding='utf-8') as f:
##    f.write(sadisplay.dot(desc))
