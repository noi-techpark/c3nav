class DefaultEditUtils:
    def __init__(self, request):
        self.request = request

    @classmethod
    def from_obj(cls, obj, request):
        return cls(request)

    @property
    def can_access_child_base_mapdata(self):
        return self.request.user_permissions.can_access_base_mapdata

    @property
    def can_create(self):
        return self.can_access_child_base_mapdata

    @property
    def _geometry_url(self):
        return None

    @property
    def geometry_url(self):
        return self._geometry_url if self.can_access_child_base_mapdata else None


class LevelChildEditUtils(DefaultEditUtils):
    def __init__(self, level, request):
        super().__init__(request)
        self.level = level

    @classmethod
    def from_obj(cls, obj, request):
        return cls(obj.level, request)

    @property
    def _geometry_url(self):
        return '/api/v2/editor/geometries/level/' + str(self.level.primary_level_pk)  # todo: resolve correctly


class SpaceChildEditUtils(DefaultEditUtils):
    def __init__(self, space, request):
        super().__init__(request)
        self.space = space

    @classmethod
    def from_obj(cls, obj, request):
        return cls(obj.space, request)

    @property
    def can_access_child_base_mapdata(self):
        return (self.request.user_permissions.can_access_base_mapdata or
                self.space.base_mapdata_accessible or
                self.space.pk in self.request.user_space_accesses)

    @property
    def _geometry_url(self):
        return '/api/v2/editor/geometries/space/'+str(self.space.pk)  # todo: resolve correctly


def clone_level_items(request, source_level_id, target_level_id, items):
    """
    Clone selected map items from one level to another.
    
    Args:
        request: Django request object
        source_level_id: ID of the source level
        target_level_id: ID of the target level  
        items: List of items to clone (each with item_type and item_id)
        
    Returns:
        Dictionary with success status, cloned items list, and message
    """
    from django.apps import apps
    from django.contrib.contenttypes.models import ContentType
    from c3nav.mapdata.models import Level
    from c3nav.editor.api.schemas import CloneFloorResponseSchema
    
    # Get the source and target levels
    try:
        source_level = Level.objects.get(pk=source_level_id)
        target_level = Level.objects.get(pk=target_level_id)
    except Level.DoesNotExist:
        return CloneFloorResponseSchema(
            success=False,
            cloned_items=[],
            message="Source or target level not found"
        ).model_dump(mode="json")
    
    # Check if user has editor permissions (simplified check for API)
    if not hasattr(request, 'user') or not request.user.is_authenticated:
        return CloneFloorResponseSchema(
            success=False,
            cloned_items=[],
            message="Authentication required"
        ).model_dump(mode="json")
    
    cloned_items = []
    
    # Define supported item types and their model mappings
    SUPPORTED_TYPES = {
        'area': 'Area',
        'obstacle': 'Obstacle', 
        'lineobstacle': 'LineObstacle',
        'stair': 'Stair',
        'ramp': 'Ramp',
        'hole': 'Hole',
        'column': 'Column',
        'poi': 'POI',
        'altitudemarker': 'AltitudeMarker',
        'space': 'Space',
        'building': 'Building',
        'door': 'Door'
    }
    
    try:
        print(f"Starting to process {len(items)} items")
        
        for i, item in enumerate(items):
            item_type = item.item_type.lower()
            item_id = item.item_id
            
            print(f"Processing item {i+1}/{len(items)}: {item_type} with ID {item_id}")
            
            if item_type not in SUPPORTED_TYPES:
                print(f"Item type '{item_type}' not supported. Supported types: {list(SUPPORTED_TYPES.keys())}")
                continue
                
            model_name = SUPPORTED_TYPES[item_type]
            Model = apps.get_model('mapdata', model_name)
            
            print(f"Looking for {model_name} with ID {item_id}")
            
            # Get the original item
            try:
                original_item = Model.objects.get(pk=item_id)
                print(f"Found original item: {original_item}")
            except Model.DoesNotExist:
                print(f"{model_name} with ID {item_id} not found")
                continue
            
            # Prepare the clone data
            clone_data = {}
            print(f"Model fields: {[f.name for f in Model._meta.fields]}")
            
            # Handle different item types differently
            if item_type == 'space':
                # For spaces, we need level but no space reference
                print(f"Processing space item with fields: {[f.name for f in Model._meta.fields]}")
                for field in Model._meta.fields:
                    if field.name in ['id', 'pk']:
                        continue
                    
                    # Skip auto fields and read-only fields
                    if (hasattr(field, 'auto_created') and field.auto_created) or \
                       (hasattr(field, 'editable') and not field.editable):
                        print(f"Skipping field {field.name}: auto_created={getattr(field, 'auto_created', False)}, editable={getattr(field, 'editable', True)}")
                        continue
                    
                    try:
                        field_value = getattr(original_item, field.name)
                        print(f"Field {field.name}: {field_value} (type: {type(field_value)})")
                    except (AttributeError, ValueError) as e:
                        print(f"Could not get field {field.name}: {e}")
                        continue
                    
                    # Handle level reference
                    if field.name == 'level':
                        clone_data[field.name] = target_level
                        print(f"Set level to target_level: {target_level}")
                    else:
                        # Copy other fields - but check for special fields that shouldn't be copied
                        if field.name in ['slug']:
                            # Don't copy slug directly as it needs to be unique
                            # Instead, create a new unique slug based on the original
                            if field_value:
                                import re
                                base_slug = re.sub(r'-\d+$', '', field_value)  # Remove trailing numbers
                                new_slug = f"{base_slug}-clone"
                                # Make sure the new slug is unique
                                counter = 1
                                test_slug = new_slug
                                while Model.objects.filter(slug=test_slug).exists():
                                    test_slug = f"{new_slug}-{counter}"
                                    counter += 1
                                clone_data[field.name] = test_slug
                                print(f"Generated unique slug: {test_slug}")
                            continue
                        if field_value is not None:
                            clone_data[field.name] = field_value
                            print(f"Copied field {field.name}: {field_value}")
                            
                print(f"Final space clone data: {clone_data}")
            else:
                # For space-related items (areas, obstacles, etc.)
                space_found = False
                for field in Model._meta.fields:
                    if field.name in ['id', 'pk']:
                        continue
                    
                    # Skip auto fields and read-only fields
                    if (hasattr(field, 'auto_created') and field.auto_created) or \
                       (hasattr(field, 'editable') and not field.editable):
                        continue
                    
                    try:
                        field_value = getattr(original_item, field.name)
                    except (AttributeError, ValueError):
                        continue
                    
                    # Handle level reference
                    if field.name == 'level':
                        clone_data[field.name] = target_level
                    # Handle space reference - need to find equivalent space on target level
                    elif field.name == 'space':
                        if hasattr(original_item, 'space') and original_item.space:
                            original_space = original_item.space
                            # Try to find a space with the same slug/title on target level
                            try:
                                target_space = target_level.spaces.filter(
                                    title=original_space.title
                                ).first()
                                if target_space:
                                    clone_data[field.name] = target_space
                                    space_found = True
                                    print(f"Found target space: {target_space}")
                                else:
                                    print(f"No equivalent space found for '{original_space.title}' on target level")
                            except Exception as e:
                                print(f"Error finding target space: {e}")
                    else:
                        # Copy other fields
                        if field_value is not None:
                            clone_data[field.name] = field_value
                
                # Skip space-related items if no equivalent space found
                if 'space' in [f.name for f in Model._meta.fields] and not space_found:
                    print(f"Skipping {item_type} {item_id} because no equivalent space found")
                    continue
                    
                print(f"Clone data for {item_type} {item_id}: {clone_data}")
            
            # Create the cloned item
            try:
                print(f"Attempting to clone {model_name} with data: {clone_data}")
                print(f"Creating {model_name} object...")
                cloned_item = Model(**clone_data)
                print(f"Created object, now saving...")
                cloned_item.save()
                print(f"Successfully created cloned item with ID: {cloned_item.pk}")
            except Exception as create_error:
                print(f"Error creating {model_name}: {create_error}")
                print(f"Error type: {type(create_error)}")
                print(f"Clone data was: {clone_data}")
                
                # Try a different approach - create empty object and set fields one by one
                try:
                    print("Trying field-by-field approach...")
                    cloned_item = Model()
                    for field_name, field_value in clone_data.items():
                        try:
                            setattr(cloned_item, field_name, field_value)
                            print(f"Set {field_name} = {field_value}")
                        except Exception as field_error:
                            print(f"Could not set {field_name}={field_value}: {field_error}")
                    cloned_item.save()
                    print(f"Successfully created item using setattr approach with ID: {cloned_item.pk}")
                except Exception as setattr_error:
                    print(f"Setattr approach also failed: {setattr_error}")
                    continue  # Skip this item
            
            cloned_items.append({
                'item_type': item_type,
                'original_id': item_id,
                'cloned_id': cloned_item.pk
            })
            
            print(f"Successfully added item {i+1} to cloned_items list")
            
        print(f"Finished processing. Total cloned items: {len(cloned_items)}")
        return CloneFloorResponseSchema(
            success=True,
            cloned_items=cloned_items,
            message=f"Successfully cloned {len(cloned_items)} items"
        ).model_dump(mode="json")
        
    except Exception as e:
        return CloneFloorResponseSchema(
            success=False,
            cloned_items=cloned_items,
            message=f"Error during cloning: {str(e)}"
        ).model_dump(mode="json")
