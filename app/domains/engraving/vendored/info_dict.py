import numpy as np
info = {"Ice_Cube":{
				"Image":{
						"gauss":(5,5)
				},
				"Mold":{
						"removeTris":[[-22.6532, 0.0, 22.6532], [-22.6532, 0.0, -22.6532], [22.6532, 0.0, 22.6532], [22.6532, 0.0, -22.6532]],
						"rot_array":np.array([[-1, 0, 0], [0, -1, 0], [0, 0, 1]]),
						"trans_array":[-22.653202056884766, 4.749745841081676e-08, -22.653200149536133],
						"depth":2,
						"mirror":True,
						"flip_norms":True,
						"yz_swap":True,
						"corrected":False,
						"location":'Cube_5cm_Mold_Mold_Core_Insert_REV01C.STL'
				},
				"Product":{
						"removeTris":[[-25.0, 25.0, 25.0], [-25.0, 25.0, -25.0], [25.0, 25.0, -25.0], [25.0, 25.0, 25.0]],
						"rot_array":None,
						"trans_array":[-25.0,23.0,-25.0],
						"depth":2,
						"mirror":False,
						"flip_norms":False,
						"yz_swap":True,
						"corrected":False,
						"location":'Cube_5cm.STL'
				}
		},
		"Silicone_Sample":{
				"Image":{
						"gauss":(5,5)
				},
				"Mold":{
						"removeTris":[[23, 0, -23], [23, 0, 23], [-23, 0, 23], [-23, 0, -23]],
						"rot_array":np.array([[-1, 0, 0], [0, -1, 0], [0, 0, 1]]), #np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]]),
						"trans_array":[-23,0,-23],
						"depth":2,
						"mirror":False,
						"flip_norms":True,
						"yz_swap":True,
						"corrected":False,
						"location":'SiliconeSample.STL'
				},
				"Product":{
						"removeTris":[[23, 0, -23], [23, 0, 23], [-23, 0, 23], [-23, 0, -23]],
						"rot_array":None, #np.array([[1, 0, 0,], [0, 1, 0,], [0, 0, 1]]),
						"trans_array":[-23,0,-23],
						"depth":2,
						"mirror":True,
						"flip_norms":False,
						"yz_swap":True,
						"corrected":True,
						"location":'SiliconeSample_Prod.STL'
				}
		},
		"Coaster_100mm_Square":{
				"Image":{
						"gauss":(5,5)
				},
				"Mold":{
						"removeTris":[[-47.5, -5, -47.5], [47.5, -5, -47.5], [47.5, -5, 47.5], [-47.5, -5, 47.5]],
						"rot_array":np.array([[-1, 0, 0], [0, -1, 0], [0, 0, 1]]), #np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]]),
						"trans_array":[-47.5,5,-47.5],
						"depth":2,
						"mirror":False,
						"flip_norms":True,
						"yz_swap":True,
						"corrected":False,
						"location":'100mmSquare_Mold_RevA.STL'
				},
				"Product":{
						"removeTris":[[-47.5, 5, 47.5], [-47.5, 5, -47.5], [47.5, 5, -47.5], [47.5, 5, 47.5]],
						"rot_array":None, #np.array([[1, 0, 0,], [0, 1, 0,], [0, 0, 1]]),
						# y should be 5-depth
						"trans_array":[-47.5,3,-47.5],
						"depth":2,
						"mirror":False,
						"flip_norms":False,
						"yz_swap":True,
						"corrected":False,
						"location":'100mmSquare_Prod_RevA.STL'
				}
		}
		# "Coaster_100mm_Square":{
		# 		"Image":{
		# 				"gauss":(5,5)
		# 		},
		# 		"Mold":{
		# 				"removeTris":[[-47.5, -5, -47.5], [47.5, -5, -47.5], [47.5, -5, 47.5], [-47.5, -5, 47.5]],
		# 				"rot_array":None, #np.array([[-1, 0, 0], [0, -1, 0], [0, 0, 1]]), #np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]]),
		# 				"trans_array":None, #[-47.5,5,-47.5],
		# 				"depth":2,
		# 				"mirror":False,
		# 				"flip_norms":False,
		# 				"yz_swap":False,
		# 				"corrected":False,
		# 				"location":'100mmSquare_Mold_RevA.STL'
		# 		},
		# 		"Product":{
		# 				"removeTris":[[-47.5, 5, 47.5], [-47.5, 5, -47.5], [47.5, 5, -47.5], [47.5, 5, 47.5]],
		# 				"rot_array":None, #np.array([[1, 0, 0,], [0, 1, 0,], [0, 0, 1]]),
		# 				# y should be 5-depth
		# 				"trans_array":None, #[-47.5,3,-47.5],
		# 				"depth":2,
		# 				"mirror":False,
		# 				"flip_norms":False,
		# 				"yz_swap":False,
		# 				"corrected":False,
		# 				"location":'100mmSquare_Prod_RevA.STL'
		# 		}
		# }  
}